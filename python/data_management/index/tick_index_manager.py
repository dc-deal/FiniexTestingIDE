"""
TickIndexManager - Fast File Selection via Metadata Index


"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.data_origin_registry import (
    ORIGIN_METADATA_KEY,
    DataOriginRegistry,
)
from python.data_management.index.index_entry_times import entry_time_bounds
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.store.abstract_store_index import store_index_filename
from python.framework.types.data_deficiency_types import DataDeficiency, TradeIdGaps
from python.framework.types.import_types.tick_index_types import (
    TickIndexMethod,
    TickIndexRebuildReason,
    TickIndexRun,
)
from python.framework.types.store_types import StoreId
from python.framework.utils.tick_deficiency_scan import measure_tick_deficiencies

vLog = get_global_logger()


# The version of the index's own SCHEMA — bumped whenever a column is added or its meaning
# changes. It closes a trap that has no other guard here: `needs_rebuild()` compares mtimes,
# so an index written before a new column existed stays "valid" and keeps serving rows without
# it until an import happens to touch a parquet. The field was already being written and read
# by nothing; giving it a read path is what makes a column addition safe to deploy.
# The proper mechanism is `AbstractStoreIndex.LOGIC_VERSION`, which these three legacy managers
# do not have — adopting it is #583's, and this is the stopgap until then.
INDEX_SCHEMA_VERSION = b'2.4'

# How many file checks an update runs at once. A check is one stat; on a mounted drive each waits
# milliseconds and in parallel the waits overlap — measured 2026-11-09 on this container's bridged
# drive: 8,369 files took about 36 s one after another and about 2 s across 16 threads. On a
# server's local disk both stay well under a second, so the number costs nothing there.
_STAT_THREADS = 16


def _json_or_none(raw: Optional[bytes]) -> Optional[dict]:
    """
    Decode a parquet metadata value that holds a JSON object.

    A malformed or absent value answers None rather than raising: the index is built over the
    whole archive, and one unreadable block must cost that file's host and nothing else.

    Args:
        raw: The raw metadata bytes, or None when the key is absent

    Returns:
        The decoded object, or None
    """
    if not raw:
        return None
    try:
        decoded = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _stat_or_none(path: Path) -> Optional[os.stat_result]:
    """
    A file's stat, for an update's check of what changed.

    Args:
        path: The file

    Returns:
        Its stat, or None when it no longer exists
    """
    try:
        return os.stat(path)
    except FileNotFoundError:
        return None


class TickIndexManager:
    """
    Manages Parquet file index for fast time-based file selection.

    Storage: Parquet (flat table)
    Memory: Nested dict {broker_type: {symbol: [entries]}}

    Migration: Auto-converts legacy JSON index on first load.
    """

    # Index file names
    # One naming rule for every index (#486): <store_id>_index.parquet. No dot —
    # a store index is not a hidden file, and two of the seven used to be.
    INDEX_FILE_PARQUET = store_index_filename(StoreId.TICKS)
    INDEX_FILE_JSON_LEGACY = '.parquet_tick_index.json'

    def __init__(self, logger: AbstractLogger = vLog, data_dir: Optional[str] = None):
        """
        Args:
            logger: Logger instance
            data_dir: Override data directory (default: from AppConfigManager)
        """
        self.logger = logger
        self._app_config = AppConfigManager()
        self.data_dir = Path(data_dir) if data_dir else Path(
            self._app_config.get_data_processed_path())

        # Parquet index file
        self.index_file = self.data_dir / self.INDEX_FILE_PARQUET
        # Legacy JSON for migration
        self._legacy_json_file = self.data_dir / self.INDEX_FILE_JSON_LEGACY

        # {broker_type: {symbol: [files]}} - unchanged API
        self.index: Dict[str, Dict[str, List[Dict]]] = {}
        # The files the last scan could not read (see get_unreadable_files)
        self._unreadable_files: List[str] = []
        # How the last build went, update or full rebuild (see get_last_run)
        self._last_run: Optional[TickIndexRun] = None
        self.logger.info('📚 Parquet Tick Index Manager initialized.')

    # =========================================================================
    # INDEX BUILDING
    # =========================================================================

    def build_index(self, force_rebuild: bool = False, check_stale: bool = False,
                    on_file_scanned: Optional[Callable[[int, int], None]] = None) -> None:
        """
        Build or load index from Parquet files.

        Args:
            force_rebuild: Force complete rebuild, ignore existing index
            check_stale: Check if index is outdated (expensive filesystem scan)
                        Default False - assumes index is current
            on_file_scanned: Called with (file number, file count) for each file a rebuild scans
        """
        reason = TickIndexRebuildReason.ASKED_FOR if force_rebuild else None
        # An index written under an older schema is rebuilt regardless of mtimes: its rows are
        # complete for the columns that existed then and silently missing the ones added since.
        if not force_rebuild and self.index_file.exists() and self._schema_outdated():
            self.logger.info(
                '🔄 Tick index was written under an older schema — rebuilding')
            force_rebuild = True
            reason = TickIndexRebuildReason.OLDER_SCHEMA

        # Fast path: Load existing index without checking staleness
        if not force_rebuild and self.index_file.exists():
            if not check_stale:
                self._load_index()
                self.logger.info(
                    f'📚 Loaded existing tick index ({len(self.index)} broker types)')
                return

            if not self.needs_rebuild():
                self._load_index()
                self.logger.info(
                    f'📚 Loaded existing tick index ({len(self.index)} broker types)')
                return
            reason = TickIndexRebuildReason.NEWER_FILES

        # Check for legacy JSON and migrate
        if not force_rebuild and self._legacy_json_file.exists() and not self.index_file.exists():
            self.logger.info('🔄 Migrating legacy JSON index to Parquet...')
            if self._migrate_from_json():
                self.logger.info('✅ Migration complete')
                return

        self._rebuild_full(reason or TickIndexRebuildReason.NO_INDEX, on_file_scanned)

    def get_full_rebuild_reason(self) -> Optional[TickIndexRebuildReason]:
        """
        Why the next update_index would scan every file instead of only the changed ones — asked
        before it runs, by whoever shows or estimates it. Reads one file's schema at most.

        Returns:
            The reason, or None when an update will do
        """
        if not self.index_file.exists():
            return TickIndexRebuildReason.NO_INDEX
        if self._schema_outdated():
            return TickIndexRebuildReason.OLDER_SCHEMA
        if not self._holds_file_identities():
            return TickIndexRebuildReason.NO_FILE_IDENTITIES
        return None

    def _holds_file_identities(self) -> bool:
        """
        Whether the index on disk records each file's size and modification time. An index
        written before they existed is still current for every reader, but an update could
        recognise none of its files: it would read them all while counting as an update, and its
        time would be estimated as one.

        Returns:
            True when both columns are there
        """
        try:
            names = pq.read_schema(self.index_file).names
        except Exception:
            return False
        return 'file_size_bytes' in names and 'file_mtime_ns' in names

    def update_index(self, on_file_scanned: Optional[Callable[[int, int], None]] = None) -> None:
        """
        Bring the index up to the tick files on disk, reading only what changed: a file the index
        does not hold, or one whose size or modification time differs from what it recorded — a
        re-import that rewrote it. Entries of files that are gone are dropped. Without an index,
        or with one written under an older schema, every file is scanned; raising
        INDEX_SCHEMA_VERSION is therefore also how a change to what a scan measures reaches every
        file.

        Args:
            on_file_scanned: Called with (file number, file count) for each file this build scans
        """
        reason = self.get_full_rebuild_reason()
        if reason is not None:
            self._rebuild_full(reason, on_file_scanned)
            return

        self.logger.info('🔍 Updating the tick index from the files that changed...')
        start_time = time.monotonic()
        self._load_index()
        held = {entry['path']: entry
                for symbols in self.index.values()
                for entries in symbols.values()
                for entry in entries}
        parquet_files = list(self.data_dir.glob('*/ticks/**/*.parquet'))
        with ThreadPoolExecutor(max_workers=_STAT_THREADS) as pool:
            file_stats = list(pool.map(_stat_or_none, parquet_files))

        kept: List[Dict] = []
        to_scan: List[Path] = []
        present = set()
        for parquet_file, file_stat in zip(parquet_files, file_stats):
            # A file removed between the listing and its check is gone, not unreadable
            if file_stat is None:
                continue
            path = str(parquet_file.absolute())
            present.add(path)
            entry = held.get(path)
            if (entry is not None and entry.get('file_size_bytes') == file_stat.st_size
                    and entry.get('file_mtime_ns') == file_stat.st_mtime_ns):
                kept.append(entry)
            else:
                to_scan.append(parquet_file)

        scanned, self._unreadable_files = self._scan_files(to_scan, on_file_scanned)
        self.index = self._nest_entries(kept + scanned)
        self._save_index()

        elapsed = time.monotonic() - start_time
        gone = len(set(held) - present)
        self._last_run = TickIndexRun(
            method=TickIndexMethod.UPDATE, rebuild_reason=None, scanned=len(to_scan),
            unchanged=len(kept), gone=gone, elapsed_s=elapsed)
        self.logger.info(
            f'✅ Index updated: {len(to_scan)} file(s) scanned, {len(kept)} unchanged, '
            f'{gone} gone, in {elapsed:.2f}s')

    def get_last_run(self) -> Optional[TickIndexRun]:
        """
        How the last build of this manager went.

        Returns:
            The run, or None when this manager has only loaded the index
        """
        return self._last_run

    def _rebuild_full(self, reason: TickIndexRebuildReason,
                      on_file_scanned: Optional[Callable[[int, int], None]]) -> None:
        """
        Scan every tick file and write the index from them alone.

        Args:
            reason: Why every file is scanned
            on_file_scanned: Called with (file number, file count) for each file scanned
        """
        self.logger.info(
            f'🔍 Scanning Parquet files for tick index... (full rebuild: {reason.value})')
        start_time = time.monotonic()

        # Pattern: mt5/ticks/EURUSD/*.parquet
        parquet_files = list(self.data_dir.glob('*/ticks/**/*.parquet'))

        if not parquet_files:
            self.logger.warning(f'No Parquet files found in {self.data_dir}')
            self.index = {}
            self._last_run = TickIndexRun(
                method=TickIndexMethod.FULL_REBUILD, rebuild_reason=reason, scanned=0,
                unchanged=None, gone=None, elapsed_s=time.monotonic() - start_time)
            return

        entries, self._unreadable_files = self._scan_files(parquet_files, on_file_scanned)
        self.index = self._nest_entries(entries)

        self._save_index()

        elapsed = time.monotonic() - start_time
        self._last_run = TickIndexRun(
            method=TickIndexMethod.FULL_REBUILD, rebuild_reason=reason,
            scanned=len(parquet_files), unchanged=None, gone=None, elapsed_s=elapsed)
        total_files = sum(
            len(files)
            for bt in self.index.values()
            for files in bt.values()
        )
        self.logger.info(
            f'✅ Index built: {total_files} files across {len(self.index)} broker types '
            f'in {elapsed:.2f}s'
        )

    def _scan_files(self, parquet_files: List[Path],
                    on_file_scanned: Optional[Callable[[int, int], None]]
                    ) -> Tuple[List[Dict], List[str]]:
        """
        Scan tick files one by one; a file that cannot be read is named, not indexed.

        Args:
            parquet_files: The files to scan
            on_file_scanned: Called with (file number, file count) for each file

        Returns:
            (the entries of the files read, one line per file that could not be read)
        """
        entries: List[Dict] = []
        unreadable: List[str] = []
        for file_no, parquet_file in enumerate(parquet_files, 1):
            if on_file_scanned is not None:
                on_file_scanned(file_no, len(parquet_files))
            try:
                entries.append(self._scan_file(parquet_file))
            except Exception as e:
                self.logger.warning(
                    f'Failed to index {parquet_file.name}: {e}')
                unreadable.append(f'{parquet_file.name}: {e}')
        return entries, unreadable

    @staticmethod
    def _nest_entries(entries: List[Dict]) -> Dict[str, Dict[str, List[Dict]]]:
        """
        Group index entries by broker type and symbol, each list in time order.

        Args:
            entries: Flat index entries

        Returns:
            broker_type → symbol → entries, each list sorted by start time
        """
        nested: Dict[str, Dict[str, List[Dict]]] = {}
        for entry in entries:
            nested.setdefault(entry['broker_type'], {}).setdefault(entry['symbol'], []).append(entry)
        for symbols in nested.values():
            for symbol_entries in symbols.values():
                symbol_entries.sort(key=lambda x: x['start_time'] or '')
        return nested

    def _scan_file(self, parquet_file: Path) -> Dict:
        """
        Scan single Parquet file and extract metadata with statistics.
        """
        # The identity an update compares, taken before the content is read: a file rewritten
        # while it is scanned then differs from it and is scanned again by the next update
        file_stat = parquet_file.stat()
        pq_file = pq.ParquetFile(parquet_file)

        try:
            symbol = parquet_file.name.split('_')[0]
        except IndexError:
            symbol = 'UNKNOWN'

        # === BASIC METADATA ===
        first_row_group = pq_file.read_row_group(0, columns=['timestamp'])
        start_time = first_row_group['timestamp'][0].as_py()

        last_row_group_idx = pq_file.num_row_groups - 1
        last_row_group = pq_file.read_row_group(
            last_row_group_idx,
            columns=['timestamp']
        )
        end_time = last_row_group['timestamp'][-1].as_py()

        custom_metadata = pq_file.metadata.metadata
        source_file = custom_metadata.get(
            b'source_file', b'unknown').decode('utf-8')

        tick_count = pq_file.metadata.num_rows
        file_size_mb = round(file_stat.st_size / (1024 * 1024), 2)

        # === STATISTICS CALCULATION ===
        if tick_count > 50000:
            df = pd.read_parquet(parquet_file)
            sample_size = max(5000, int(tick_count * 0.1))
            df_sample = df.sample(n=min(sample_size, len(df)))

            avg_spread_points = float(
                df_sample['spread_points'].mean()) if 'spread_points' in df_sample else None
            avg_spread_pct = float(
                df_sample['spread_pct'].mean()) if 'spread_pct' in df_sample else None

            sessions = df['session'].value_counts(
            ).to_dict() if 'session' in df else {}
        else:
            df = pd.read_parquet(parquet_file)

            avg_spread_points = float(
                df['spread_points'].mean()) if 'spread_points' in df else None
            avg_spread_pct = float(df['spread_pct'].mean()
                                   ) if 'spread_pct' in df else None
            sessions = df['session'].value_counts(
            ).to_dict() if 'session' in df else {}

        duration_seconds = (end_time - start_time).total_seconds()
        tick_frequency = round(tick_count / duration_seconds,
                               2) if duration_seconds > 0 else 0.0

        broker_type_raw = custom_metadata.get(b'broker_type')
        if broker_type_raw:
            broker_type = broker_type_raw.decode('utf-8')
        else:
            broker_type = custom_metadata.get(
                b'data_collector', b'mt5').decode('utf-8')

        # === BUILD INDEX ENTRY ===
        # Arrival bounds in file order — the event time above says when a tick
        # happened, this says when it reached us. Both are needed to verify that
        # the concatenated archive stream is continuous.
        collected_start = int(df['collected_msc'].iloc[0]
                              ) if 'collected_msc' in df else 0
        collected_end = int(df['collected_msc'].iloc[-1]
                            ) if 'collected_msc' in df else 0

        # The collector's clock-correction record, cumulative over its session.
        # A file whose closing count exceeds its opening one absorbed a backward
        # step of the OS clock — which the collector clamps, so collected_msc
        # never goes backwards and the monotonicity check cannot see it. These
        # two are the only durable trace of such an event.
        anchor_resyncs = self._meta_int(
            custom_metadata, b'source_meta_anchor_resyncs')
        anchor_max_correction_ms = self._meta_int(
            custom_metadata, b'source_meta_anchor_max_correction_ms')

        # Where this file came from, as it was RESOLVED at import (#518). Read back from the
        # stamp and never re-resolved: the registry is a judgement that can be edited, so
        # asking it again would report today's meaning against a file imported under the
        # meaning of the day it arrived. 'unknown' for anything written before the stamp
        # existed — honest, and distinguishable from a resolved class.
        origin_instance_id = custom_metadata.get(
            b'origin_instance_id', b'').decode('utf-8')
        origin_class = custom_metadata.get(
            b'origin_class', b'unknown').decode('utf-8')
        origin_evidence = custom_metadata.get(
            b'origin_evidence', b'unknown').decode('utf-8')
        # Read from the producer's VERBATIM block rather than from a stamp of its own. The
        # three values above are stamped because `class` and `evidence` are JUDGEMENTS that
        # must never be re-derived; the host is neither judged nor decided on, so a second
        # copy beside the block would be the same fact written twice (§19) — and it would
        # stay empty on every file imported before the copy existed, where the block has
        # carried it all along.
        origin_collected_on = DataOriginRegistry.read_nested_collected_on(
            {ORIGIN_METADATA_KEY: _json_or_none(
                custom_metadata.get(b'source_meta_origin'))})

        # What the file LACKS, measured from its rows (#561) — here, because the index reads
        # every file it scans in full anyway. An update scans only the files that changed, so a
        # file imported before the measurement existed is measured on the next FULL rebuild,
        # without being imported again — a change to this measurement raises
        # INDEX_SCHEMA_VERSION, and that forces one.
        measurement = measure_tick_deficiencies(df)

        return {
            'file': parquet_file.name,
            'path': str(parquet_file.absolute()),
            'symbol': symbol,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'collected_start': collected_start,
            'collected_end': collected_end,
            'anchor_resyncs': anchor_resyncs,
            'anchor_max_correction_ms': anchor_max_correction_ms,
            'tick_count': tick_count,
            'file_size_mb': file_size_mb,
            'file_size_bytes': file_stat.st_size,
            'file_mtime_ns': file_stat.st_mtime_ns,
            'source_file': source_file,
            # When the importer wrote the file. A re-import that changes only VALUES keeps
            # the name, the count and the range; this is what still tells the file is new.
            'processed_at': custom_metadata.get(b'processed_at', b'').decode('utf-8'),
            'num_row_groups': pq_file.num_row_groups,

            'statistics': {
                'avg_spread_points': round(avg_spread_points, 2) if avg_spread_points else None,
                'avg_spread_pct': round(avg_spread_pct, 6) if avg_spread_pct else None,
                'tick_frequency_per_second': tick_frequency
            },

            'sessions': {str(k): int(v) for k, v in sessions.items()},

            'broker_type': broker_type,
            'data_format_version': custom_metadata.get(
                b'data_format_version', b'unknown').decode('utf-8'),
            'origin_instance_id': origin_instance_id,
            'origin_collected_on': origin_collected_on,
            'origin_class': origin_class,
            'origin_evidence': origin_evidence,
            'deficiencies': measurement.deficiencies,
            'trade_id_gaps': measurement.trade_id_gaps,
        }

    @staticmethod
    def _meta_int(custom_metadata: Dict[bytes, bytes],
                  key: bytes) -> Optional[int]:
        """
        Read an integer from the Parquet key/value metadata.

        Args:
            custom_metadata: Parquet custom metadata of the scanned file
            key: Metadata key to read

        Returns:
            The value as int, or None when the key is absent or unreadable —
            absent means the collector never reported it, which is a different
            statement from a reported zero
        """
        raw = custom_metadata.get(key)
        if raw is None:
            return None

        try:
            return int(raw.decode('utf-8'))
        except (AttributeError, UnicodeDecodeError, ValueError):
            return None

    def needs_rebuild(self) -> bool:
        """Check if index needs rebuilding."""
        if not self.index_file.exists():
            return True

        index_mtime = self.index_file.stat().st_mtime

        parquet_files = list(self.data_dir.glob('*/ticks/**/*.parquet'))
        if parquet_files:
            newest_parquet = max(f.stat().st_mtime for f in parquet_files)

            if newest_parquet > index_mtime:
                self.logger.info(
                    '📋 Tick index outdated - newer Parquet files found')
                return True

        return False

    # =========================================================================
    # FILE SELECTION
    # =========================================================================

    def get_relevant_files(
        self,
        broker_type: str,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Path]:
        """
        Find ONLY files covering requested time range for specific broker_type.
        """
        if broker_type not in self.index:
            self.logger.warning(
                f"Broker type '{broker_type}' not found in index")
            return []

        if symbol not in self.index[broker_type]:
            self.logger.warning(
                f"Symbol '{symbol}' not found in index for broker_type '{broker_type}'")
            return []

        entries = self.index[broker_type][symbol]
        starts, ends = entry_time_bounds(entries)
        return [
            Path(entry['path'])
            for entry, file_start, file_end in zip(entries, starts, ends)
            if file_start <= end_date and file_end >= start_date
        ]

    # =========================================================================
    # INDEX PERSISTENCE - PARQUET FORMAT
    # =========================================================================

    def _save_index(self) -> None:
        """Save index to Parquet file (flat structure)."""
        rows = []

        for broker_type, symbols in self.index.items():
            for symbol, entries in symbols.items():
                for entry in entries:
                    row = {
                        'broker_type': broker_type,
                        'symbol': symbol,
                        'file': entry['file'],
                        'path': entry['path'],
                        'start_time': entry['start_time'],
                        'end_time': entry['end_time'],
                        'tick_count': entry['tick_count'],
                        'file_size_mb': entry['file_size_mb'],
                        'file_size_bytes': entry.get('file_size_bytes'),
                        'file_mtime_ns': entry.get('file_mtime_ns'),
                        'source_file': entry['source_file'],
                        'processed_at': entry.get('processed_at', ''),
                        'num_row_groups': entry['num_row_groups'],
                        'collected_start': entry.get('collected_start', 0),
                        'collected_end': entry.get('collected_end', 0),
                        'anchor_resyncs': entry.get('anchor_resyncs'),
                        'anchor_max_correction_ms': entry.get(
                            'anchor_max_correction_ms'),
                        # Nested dicts as JSON strings
                        'statistics': json.dumps(entry.get('statistics', {})),
                        'sessions': json.dumps(entry.get('sessions', {})),
                        'data_format_version': entry.get(
                            'data_format_version', 'unknown'),
                        'origin_instance_id': entry.get('origin_instance_id', ''),
                        'origin_class': entry.get('origin_class', 'unknown'),
                        'origin_evidence': entry.get('origin_evidence', 'unknown'),
                        'origin_collected_on': entry.get('origin_collected_on', ''),
                        'deficiencies': ','.join(
                            deficiency.value for deficiency in entry.get('deficiencies', [])),
                        'trade_id_gaps': (
                            json.dumps(asdict(entry['trade_id_gaps']))
                            if entry.get('trade_id_gaps') else ''),
                    }
                    rows.append(row)

        # One column list for both branches - a second, drifting list is how
        # data_format_version got lost on persist in the first place.
        columns = [
            'broker_type', 'symbol', 'file', 'path', 'start_time', 'end_time',
            'tick_count', 'file_size_mb', 'file_size_bytes', 'file_mtime_ns',
            'source_file', 'processed_at', 'num_row_groups',
            'collected_start', 'collected_end',
            'anchor_resyncs', 'anchor_max_correction_ms',
            'statistics', 'sessions', 'data_format_version',
            'origin_instance_id', 'origin_collected_on', 'origin_class',
            'origin_evidence', 'deficiencies', 'trade_id_gaps',
        ]
        df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(
            columns=columns)
        # One conversion per column, not per row: per row it took about 4.7 s over 8,369 files
        # (measured 2026-11-09), the column takes hundredths of a second. ISO8601, because the
        # times come with and without a fraction of a second
        for column in ('start_time', 'end_time'):
            df[column] = pd.to_datetime(df[column], format='ISO8601')
        # Exact integers, never floats: a nanosecond mtime does not survive a float64, and an
        # entry loaded from an index written before these two columns existed has None here
        for column in ('file_size_bytes', 'file_mtime_ns'):
            df[column] = pd.array([row[column] for row in rows], dtype='Int64')

        # Add metadata
        metadata = {
            b'created_at': datetime.now(timezone.utc).isoformat().encode(),
            b'data_dir': str(self.data_dir).encode(),
            b'index_version': INDEX_SCHEMA_VERSION
        }

        table = pa.Table.from_pandas(df)
        table = table.replace_schema_metadata(
            {**table.schema.metadata, **metadata})

        pq.write_table(table, self.index_file)
        self.logger.debug(f'💾 Tick index saved to {self.index_file}')

    def _schema_outdated(self) -> bool:
        """
        Whether the index on disk was written under an older schema version.

        An unreadable or unstamped index counts as outdated: every index written before the
        field had a read path is exactly the one whose columns cannot be trusted.

        Returns:
            True when the stored version differs from the current one
        """
        try:
            stored = pq.read_schema(self.index_file).metadata or {}
        except Exception:
            return True
        return stored.get(b'index_version') != INDEX_SCHEMA_VERSION

    def _load_index(self) -> None:
        """Load index from Parquet file and convert to nested dict."""
        try:
            # Straight to Python records, without a DataFrame: every backtest batch loads the
            # index, and a Series built per row cost 0.65–0.8 s over 7,726 rows where this
            # read costs about 0.1 s (measured 2026-10-15)
            records = pq.read_table(self.index_file).to_pylist()
            self.index = self._records_to_nested_dict(records)
        except Exception as e:
            self.logger.warning(f'Failed to load tick index: {e}')
            self.index = {}

    def _records_to_nested_dict(self, records: List[Dict]) -> Dict[str, Dict[str, List[Dict]]]:
        """
        Convert the index's flat rows to the nested dict structure.

        Args:
            records: One dict per index row, as the parquet file holds it

        Returns:
            broker_type → symbol → entries, each list sorted by start time
        """
        entries = []

        for row in records:
            broker_type = row['broker_type']
            symbol = row['symbol']

            entry = {
                'file': row['file'],
                'path': row['path'],
                'symbol': symbol,
                'start_time': row['start_time'].isoformat() if pd.notna(row['start_time']) else None,
                'end_time': row['end_time'].isoformat() if pd.notna(row['end_time']) else None,
                'tick_count': int(row['tick_count']),
                'file_size_mb': float(row['file_size_mb']),
                # Tolerant, and not a schema change: only update_index reads these two. An index
                # without them is rebuilt in full by the next update (get_full_rebuild_reason),
                # and an entry without them is read again by it
                'file_size_bytes': row.get('file_size_bytes'),
                'file_mtime_ns': row.get('file_mtime_ns'),
                'source_file': row['source_file'],
                'num_row_groups': int(row['num_row_groups']),
                # Tolerant: index files written before the arrival bounds were
                # persisted have no such column - they read as 0 until rebuilt.
                'collected_start': int(row['collected_start']) if 'collected_start' in row and pd.notna(row['collected_start']) else 0,
                'collected_end': int(row['collected_end']) if 'collected_end' in row and pd.notna(row['collected_end']) else 0,
                # Tolerant, and None rather than 0: an index written before the
                # anchor counters were persisted has no such column, and a file
                # predating format 1.4.0 never carried them. Both mean "not
                # reported", never "no correction happened".
                'anchor_resyncs': int(row['anchor_resyncs']) if 'anchor_resyncs' in row and pd.notna(row['anchor_resyncs']) else None,
                'anchor_max_correction_ms': int(row['anchor_max_correction_ms']) if 'anchor_max_correction_ms' in row and pd.notna(row['anchor_max_correction_ms']) else None,
                'statistics': json.loads(row['statistics']) if row['statistics'] else {},
                'sessions': json.loads(row['sessions']) if row['sessions'] else {},
                'broker_type': broker_type,
                # Tolerant: index files written before the field was persisted
                # have no such column - they read as 'unknown' until rebuilt.
                'data_format_version': row.get('data_format_version', 'unknown'),
                # Tolerant in the same way, and for the same reason: an index written before
                # the origin stamp existed has no such column. 'unknown' is the honest reading
                # and is exactly what the gate refuses for a measurement run.
                'origin_instance_id': row.get('origin_instance_id', ''),
                'origin_class': row.get('origin_class', 'unknown'),
                'origin_evidence': row.get('origin_evidence', 'unknown'),
                'origin_collected_on': row.get('origin_collected_on', ''),
                # Written by every index of this schema version, so no tolerance is needed:
                # an older index is rebuilt before it is read (_schema_outdated)
                'processed_at': row['processed_at'],
                'deficiencies': [
                    DataDeficiency(value) for value in (row['deficiencies'] or '').split(',')
                    if value],
                'trade_id_gaps': (
                    TradeIdGaps(**json.loads(row['trade_id_gaps']))
                    if row['trade_id_gaps'] else None),
            }

            entries.append(entry)

        return self._nest_entries(entries)

    def _migrate_from_json(self) -> bool:
        """Migrate from legacy JSON format to Parquet."""
        try:
            with open(self._legacy_json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.index = data.get('symbols', {})

            self._save_index()

            # Optionally rename old file
            backup_path = self._legacy_json_file.with_suffix('.json.bak')
            self._legacy_json_file.rename(backup_path)
            self.logger.info(f'📦 Legacy JSON backed up to {backup_path}')

            return True
        except Exception as e:
            self.logger.error(f'Migration failed: {e}')
            return False

    # =========================================================================
    # LEGACY COMPATIBILITY - save_index / load_index public methods
    # =========================================================================

    def save_index(self) -> None:
        """Public method for saving index (backwards compatible)."""
        self._save_index()

    def load_index(self) -> None:
        """Public method for loading index (backwards compatible)."""
        self._load_index()

    # =========================================================================
    # FILE COVERAGE
    # =========================================================================

    def get_unreadable_files(self) -> List[str]:
        """
        The files the last scan could not read — each one is missing from the index, which
        announces nothing on its own, so a caller counts them.

        Returns:
            One 'filename: error' entry per file; empty when the scan read every file, or
            when the index was loaded instead of scanned
        """
        return list(self._unreadable_files)

    def get_symbol_entries(self, broker_type: str, symbol: str) -> List[Dict]:
        """
        Get all index entries for a symbol, chronologically sorted.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol

        Returns:
            List of index entries, empty when the pair is not indexed
        """
        return self.index.get(broker_type, {}).get(symbol, [])

    def get_symbol_file_coverage(self, broker_type: str, symbol: str) -> Dict:
        """Get basic coverage statistics for a symbol."""
        if broker_type not in self.index:
            return {}

        if symbol not in self.index[broker_type]:
            return {}

        entries = self.index[broker_type][symbol]

        return {
            'num_files': len(entries),
            'total_ticks': sum(e['tick_count'] for e in entries),
            'total_size_mb': sum(e['file_size_mb'] for e in entries),
            'start_time': entries[0]['start_time'],
            'end_time': entries[-1]['end_time'],
            'files': [e['file'] for e in entries]
        }

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def identities_on_several_hosts(self) -> Dict[str, List[str]]:
        """
        Identities this archive has seen on more than one producing host.

        An identity belongs to a DATA ROOT, never to a machine, so a move is legitimate and
        this is not an error — it is the one observation that separates two cases the data
        itself cannot: a RESTORE, where the same series continues on new hardware, and a
        COPY, where two writers now assert one identity. Only the operator can say which,
        and they can only say it if somebody noticed.

        Derived at the INDEX rather than at import on purpose: the index sees every file for
        an identity at once, where an importer mid-batch would compare against a stale
        picture and miss a change that arrived inside its own run.

        Files carrying no host contribute nothing rather than an empty group — an absent
        value is not a second host.

        Returns:
            Identity -> the hosts it was seen on, sorted; identities on one host are omitted
        """
        hosts: Dict[str, set] = {}
        for symbols in self.index.values():
            for entries in symbols.values():
                for entry in entries:
                    identity = entry.get('origin_instance_id', '')
                    host = entry.get('origin_collected_on', '')
                    if identity and host:
                        hosts.setdefault(identity, set()).add(host)
        return {identity: sorted(seen)
                for identity, seen in hosts.items() if len(seen) > 1}

    def list_symbols(self, broker_type: Optional[str] = None) -> List[str]:
        """List all available symbols."""
        if broker_type:
            if broker_type not in self.index:
                return []
            return sorted(self.index[broker_type].keys())

        all_symbols = set()
        for bt in self.index:
            all_symbols.update(self.index[bt].keys())
        return sorted(all_symbols)

    def list_broker_types(self) -> List[str]:
        """List all available broker types."""
        return sorted(self.index.keys())

    def print_summary(self) -> None:
        """Print index summary grouped by broker_type."""
        print('\n' + '='*60)
        print('📚 Parquet Tick Index Summary')
        print('='*60)

        if not self.index:
            print('   (empty tick index)')
            return

        for broker_type in sorted(self.index.keys()):
            print(f'\n📂 {broker_type}:')

            for symbol in sorted(self.index[broker_type].keys()):
                coverage = self.get_symbol_file_coverage(broker_type, symbol)
                print(f'   {symbol}:')
                print(f"      Files:  {coverage['num_files']}")
                print(f"      Ticks:  {coverage['total_ticks']:,}")
                print(f"      Size:   {coverage['total_size_mb']:.1f} MB")
                print(
                    f"      Range:  {coverage['start_time'][:10]} → {coverage['end_time'][:10]}")

        # Printed here because this is where an operator already looks at the archive as a
        # whole, and the question is archive-wide rather than per symbol. Silence is the
        # normal answer and says the check ran.
        moved = self.identities_on_several_hosts()
        if moved:
            print('\n⚠️  One identity, several producing hosts:')
            for identity, hosts in sorted(moved.items()):
                print(f"   {identity}  →  {', '.join(hosts)}")
            print('   An identity belongs to a data root, so a MOVE is legitimate and a COPY')
            print('   is not — and only the operator can say which this was.')

        print('='*60 + '\n')
