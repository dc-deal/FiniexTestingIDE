"""
FiniexTestingIDE Tick Data Importer
===================================

Converts MQL5 JSON exports to optimized Parquet files with UTC conversion.
Workflow: Load JSON → Validate → Optimize → UTC Conversion → Save Parquet

Author: FiniexTestingIDE Team
Version: 1.6 (Import Config Isolation + Source Metadata)
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration.data_origin_registry import DataOriginRegistry
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.importers.bar_importer import BarImporter
from python.data_management.index.tick_index_manager import TickIndexManager

# Import duplicate detection
from python.framework.exceptions.data_quality_errors import (
    ArtificialDuplicateException,
    TickFileValidationException,
)
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.reporting.duplicate_report import DuplicateReport
from python.framework.types.config_types.market_config_types import ServerClockConfig
from python.framework.types.import_schema_types import (
    ALREADY_CAPTURED_METADATA_KEYS,
    DISCARDED_METADATA_KEYS,
    NESTED_METADATA_KEYS,
)
from python.framework.types.validation_types import TickFileValidationResult
from python.framework.utils.market_session_utils import get_session_from_utc_hour
from python.framework.utils.time_utils import server_clock_to_utc_ms
from python.framework.validators.tick_import_validator import TickImportValidator

vLog = get_global_logger()


def _server_clock_label(server_clock: ServerClockConfig) -> str:
    """
    Render a server clock rule the way logs and parquet headers state it.

    Args:
        server_clock: The broker's server clock rule

    Returns:
        The rule as text, e.g. 'America/New_York+7h' or 'UTC+0h'
    """
    return f'{server_clock.timezone}+{server_clock.hours_ahead}h'


class TickDataImporter:
    """
    Converts MQL5 JSON exports to Parquet format with UTC conversion.

    Main features:
    - JSON → Parquet conversion (10:1 compression)
    - Datatype optimization for performance
    - UTC conversion through each broker's server clock rule (market_config.json)
    - Session recalculation based on UTC
    - Structural validation (refuses defective files, never repairs)
    - Batch processing with error handling
    - Duplicate prevention with override option
    - Hierarchical directory structure
    - Market type detection (v1.4+)
    """

    VERSION = '1.6'

    def __init__(self, source_dir: str, target_dir: str,
                 override: bool = False,
                 move_processed_files: bool = True,
                 finished_dir: Optional[str] = None,
                 auto_render_bars: bool = True):
        """
        Initialize importer with source and target paths.

        Args:
            source_dir: MQL5 JSON export directory
            target_dir: Parquet target directory (import_output)
            override: Overwrite existing files
            move_processed_files: Move JSON to finished_dir after import
            finished_dir: Directory for processed JSON files
            auto_render_bars: Automatically render bars after tick import
        """
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.override = override
        # Per broker: how many files resolved to each UTC offset — the summary's evidence that
        # a season boundary was crossed rather than one constant applied to everything.
        self._applied_offsets: Dict[str, Dict[str, int]] = {}
        self._move_processed_files = move_processed_files
        self._finished_dir = Path(finished_dir) if finished_dir else None
        self._auto_render_bars = auto_render_bars

        # Batch processing statistics
        self.processed_files = 0
        self.total_ticks = 0
        self.errors = []
        self.warnings = []

        # Track processed broker_types for bar rendering
        self._processed_broker_types: Set[str] = set()

        self._validator = TickImportValidator()

    def _normalize_broker_type(self, broker_type: str) -> str:
        """
        Normalize broker_type for filesystem use.

        Args:
            broker_type: Raw broker_type string

        Returns:
            Filesystem-safe normalized string
        """
        normalized = broker_type.lower().strip()
        # Replace anything not alphanumeric or underscore
        normalized = re.sub(r'[^a-z0-9_]', '_', normalized)
        return normalized

    def process_all_exports(self):
        """
        Finds all TickCollector exports and converts them sequentially.
        Errors do not stop processing of remaining files.
        """
        json_files = list(self.source_dir.glob('*_ticks.json'))

        if not json_files:
            vLog.warning(
                f'No JSON files found in {self.source_dir}. Just rebuilding index.')
            self.rebuild_parquet_index()
            return

        vLog.info('\n' + '=' * 80)
        vLog.info(f'FiniexTestingIDE Tick Data Importer V{self.VERSION}')
        vLog.info('=' * 80)
        vLog.info(f'Found: {len(json_files)} JSON files')
        vLog.info(
            f"Override Mode: {'ENABLED' if self.override else 'DISABLED'}")
        market_config = MarketConfigManager()
        for broker_type in market_config.get_all_broker_types():
            clock = market_config.get_server_clock(broker_type)
            vLog.info(f'Server clock: {broker_type} → {_server_clock_label(clock)}')
        vLog.info('=' * 80 + '\n')

        # Sequential processing with error recovery
        for json_file in json_files:
            vLog.info(f'\n📄 Processing: {json_file.name}')
            try:
                self.convert_json_to_parquet(json_file)
                self.processed_files += 1
            except ArtificialDuplicateException as e:
                # Special handling for duplicate detection
                warning_msg = f'DUPLICATE DETECTED in {json_file.name}'
                vLog.warning(warning_msg)
                vLog.warning(str(e))
                self.warnings.append(warning_msg)
                vLog.info('→ Skipping import (duplicate already exists)')
            except TickFileValidationException as e:
                # Structural defect in the source file — refuse it, keep the batch running
                error_msg = f'VALIDATION FAILED in {json_file.name}'
                vLog.error(error_msg)
                vLog.error(str(e))
                self.errors.append(error_msg)
                vLog.info('→ Skipping import (file not written)')
            except Exception as e:
                error_msg = f'ERROR in {json_file.name}: {str(e)}'
                vLog.error(error_msg)
                self.errors.append(error_msg)

        self.rebuild_parquet_index()

        # === AUTO-TRIGGER BAR RENDERING ===
        # After all ticks imported, render bars automatically
        if self.processed_files > 0 and self._auto_render_bars:
            self._trigger_bar_rendering()

        self._print_summary()

    def rebuild_parquet_index(self):
        """Rebuild index after successful imports"""
        vLog.info('\n🔄 Rebuilding Parquet index...')
        try:

            index_manager = TickIndexManager(data_dir=str(self.target_dir))
            index_manager.build_index(force_rebuild=True)

            symbols = index_manager.list_symbols()
            vLog.info(f'✅ Index rebuilt: {len(symbols)} symbols indexed')

            self._validate_archive_ordering(index_manager)

        except Exception as e:
            vLog.error(f'❌ Failed to rebuild index: {e}')
            vLog.error('   Index may be outdated - run manual rebuild!')

    def _validate_archive_ordering(self, index_manager: TickIndexManager) -> None:
        """
        Check the archive across file boundaries: no overlapping event ranges
        per symbol, and collected_msc continuous from one file to the next.

        Runs off the index, so no data file is opened. Which files follow each
        other is decided by their tick bounds, not by name or header.

        Args:
            index_manager: The freshly built tick index
        """
        entries = {
            broker_type: {
                symbol: index_manager.get_symbol_entries(broker_type, symbol)
                for symbol in index_manager.list_symbols(broker_type)
            }
            for broker_type in index_manager.list_broker_types()
        }

        findings = self._validator.validate_archive_ordering(entries)
        if not findings:
            vLog.info(
                '✅ Archive ordering verified: no overlapping coverage, '
                'collected_msc continuous across file boundaries'
            )
            return

        vLog.warning(f'⚠️  {len(findings)} archive ordering findings:')
        for finding in findings:
            vLog.warning(f'   {finding}')
            self.warnings.append(finding)

    def convert_json_to_parquet(self, json_file: Path):
        """
        Converts single JSON file to optimized Parquet with UTC conversion.

        Pipeline:
        1. Load JSON and validate structure
        2. Create DataFrame and optimize datatypes
        3. Apply time offset (if set)
        4. Recalculate sessions (if offset applied)
        5. Validate structural invariants (refuses the file on violation)
        6. Check for existing duplicates (with override support)
        7. Save as Parquet with metadata
        """

        # ===========================================
        # 1. LOAD AND VALIDATE JSON
        # ===========================================

        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if 'ticks' not in data or 'metadata' not in data:
            raise ValueError(
                "Invalid JSON structure - missing 'ticks' or 'metadata'")

        ticks = data['ticks']
        metadata = data['metadata']

        if not ticks:
            vLog.warning(f'No ticks in {json_file.name}')
            return

        # ===========================================
        # 2. DISPLAY BROKER METADATA (FIXED)
        # ===========================================

        vLog.info('📊 Broker Metadata:')

        # Get data version to show in "not available" messages
        data_version = metadata.get('data_format_version', 'unknown')

        # Detected Offset (available since v1.0.5)
        detected_offset = metadata.get('broker_utc_offset_hours', None)
        if detected_offset is not None:
            sign = '+' if detected_offset >= 0 else ''
            vLog.info(f'   Detected Offset: GMT{sign}{detected_offset}')
        else:
            vLog.info(
                f'   Detected Offset: Not available (pre v1.0.5 data, version: {data_version})')

        # Local Device Time (planned for v1.0.5+, but not yet implemented in MQL5)
        local_device = metadata.get('local_device_time', None)
        if local_device:
            vLog.info(f'   Local Device:    {local_device}')
        else:
            vLog.info('   Local Device:    Not available (pre v1.0.5 data)')

        # Broker Time (planned for v1.0.5+, but not yet implemented in MQL5)
        broker_time = metadata.get('broker_server_time', None)
        if broker_time:
            vLog.info(f'   Broker Time:     {broker_time}')
        else:
            vLog.info('   Broker Time:     Not available (pre v1.0.5 data)')

        # ===========================================
        # 3. CREATE AND OPTIMIZE DATAFRAME
        # ===========================================

        df = pd.DataFrame(ticks)

        # Ensure collected_msc column exists (missing in pre-V1.3.0 data)
        if 'collected_msc' not in df.columns:
            df['collected_msc'] = 0

        df = self._optimize_datatypes(df)

        # Parse timestamps as timezone-naive
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])

        # ===========================================
        # 4. CONVERT THE SERVER CLOCK TO UTC (the broker's rule)
        # ===========================================
        broker_type_normalized = self._validate_broker_type(
            metadata)

        server_clock = MarketConfigManager().get_server_clock(broker_type_normalized)
        clock_label = _server_clock_label(server_clock)
        should_apply_offset = not (server_clock.timezone == 'UTC'
                                   and server_clock.hours_ahead == 0)
        file_offset = '0'

        if should_apply_offset:
            try:
                df, file_offset = self._apply_server_clock(df, server_clock)
            except ValueError as e:
                # A stamp in the hour a daylight saving change repeats or skips has no single
                # UTC time — a property of this file, so it is refused like any other defect.
                refusal = TickFileValidationResult(is_valid=True, file_name=json_file.name)
                refusal.add_error(f'Server clock {clock_label}: {e}')
                raise TickFileValidationException(refusal) from e
            df = self._recalculate_sessions(df)
            vLog.info(
                f'   ✅ Server clock {clock_label} → UTC offset {file_offset}h '
                f'(broker_type={broker_type_normalized})')
            vLog.info('   ✅ Sessions recalculated based on UTC time')
        else:
            vLog.info(
                f'   ℹ️  Server clock {clock_label} for broker_type={broker_type_normalized} '
                f'— already UTC')

        # ===========================================
        # 6. VALIDATION
        # ===========================================

        self._processed_broker_types.add(broker_type_normalized)
        # Preserve JSON array order (= authentic arrival order)
        # No sort — collected_msc monotonicity depends on this
        df = df.reset_index(drop=True)

        # Read once, here, because the validator needs it before the stamp does. It decides
        # two different things: whether a missing origin block is a defect, and what goes into
        # the parquet header.
        data_format_version = metadata.get('data_format_version', '1.0.0')

        validation = self._validator.validate_file(
            df=df,
            file_name=json_file.name,
            declared_tick_count=data.get('summary', {}).get('total_ticks'),
            collected_msc_is_utc=metadata.get(
                'collected_msc_timebase') == 'utc',
            # Where trades print centrally, a tick without a traded price is malformed
            # producer output. Refusing here is what keeps `price_formation` a checked
            # expectation instead of a declaration the data may quietly contradict.
            price_formation=MarketConfigManager().get_price_formation(
                broker_type_normalized),
            # From the origin boundary on, a file that names no producer is a defect rather
            # than history. Passed here rather than resolved inside the validator so that
            # exactly one place reads the block — the same reason the resolution below is
            # done once and stamped.
            data_format_version=data_format_version,
            stated_instance_id=DataOriginRegistry.read_nested_instance_id(metadata),
            # A week that opens inside the file must open at the market's day anchor — the
            # one check that sees a wrong server clock on a file whose arrival time was
            # restored from its own event time. Markets without a weekend skip it.
            weekend_anchor=(
                MarketConfigManager().get_trading_day_anchor(broker_type_normalized)
                if MarketConfigManager().has_weekend_closure(broker_type_normalized)
                else None),
        )
        for warning in validation.warnings:
            vLog.warning(f'   ⚠️  {warning}')
            self.warnings.append(f'{json_file.name}: {warning}')
        if not validation.is_valid:
            raise TickFileValidationException(validation)
        vLog.info('   ✅ Validation passed')

        # ===========================================
        # 7. PREPARE PARQUET OUTPUT
        # ===========================================

        symbol = metadata.get('symbol', 'UNKNOWN')
        start_time = pd.to_datetime(metadata.get(
            'start_time', datetime.now(timezone.utc)))


        # Get market_type from MarketConfigManager (Single Source of Truth)
        market_config = MarketConfigManager()
        market_type = market_config.get_market_type(
            broker_type_normalized).value

        target_path = self.target_dir / broker_type_normalized / 'ticks' / symbol
        target_path.mkdir(parents=True, exist_ok=True)

        parquet_name = f"{symbol}_{start_time.strftime('%Y%m%d_%H%M%S')}.parquet"
        parquet_path = target_path / parquet_name

        # Where this file came from, resolved ONCE and stamped (#518). Resolved here rather
        # than read back later for the same reason the price basis is: the registry is a
        # judgement that can be edited, so a surface re-resolving it would report the meaning
        # of TODAY against a file imported under the meaning of the day it arrived. The
        # identity itself travels verbatim beside it, as `source_meta_origin`.
        origin = DataOriginRegistry().resolve(
            DataOriginRegistry.read_nested_instance_id(metadata), data_format_version,
            broker_type=broker_type_normalized)

        # Metadata for Parquet header
        parquet_metadata = {
            'source_file': json_file.name,
            'symbol': symbol,
            'broker': metadata.get('broker', 'unknown'),
            'data_format_version': data_format_version,
            'broker_type': broker_type_normalized,
            'market_type': market_type,
            'processed_at': datetime.now(timezone.utc).isoformat(),
            'tick_count': str(len(df)),
            'importer_version': self.VERSION,
            # The UTC offset(s) the rule resolved to for THIS file's ticks — '-2' for an MT5
            # file in US winter, '-3' in summer, both comma-joined across a season change.
            'user_time_offset_hours': file_offset,
            'server_clock_rule': clock_label,
            'utc_conversion_applied': 'true' if should_apply_offset else 'false',
            'origin_instance_id': origin.instance_id or '',
            'origin_class': origin.origin_class.value,
            'origin_evidence': origin.evidence.value,
        }

        # Preserve original MQL5 metadata for traceability (source_meta_ prefix)
        # Both key lists are declared in import_schema_types, beside the schema
        # they describe. A nested block that is missing from NESTED_METADATA_KEYS
        # falls to str() and lands as a Python repr rather than as JSON.
        for meta_key, meta_value in metadata.items():
            if meta_key in DISCARDED_METADATA_KEYS:
                continue
            if meta_key in NESTED_METADATA_KEYS:
                parquet_metadata[f'source_meta_{meta_key}'] = json.dumps(
                    meta_value)
            elif meta_key not in ALREADY_CAPTURED_METADATA_KEYS:
                parquet_metadata[f'source_meta_{meta_key}'] = str(meta_value)

        # ===========================================
        # 8. CHECK FOR EXISTING DUPLICATES
        # ===========================================

        vLog.debug('Checking for existing duplicates...')
        duplicate_report = self._check_for_existing_duplicate(
            json_file.name,
            broker_type_normalized,
            symbol,
            parquet_path
        )

        if duplicate_report:
            if self.override:
                vLog.warning('⚠️  Override enabled - deleting existing file')
                for dup_file in duplicate_report.duplicate_files:
                    dup_file.unlink()
                    vLog.info(f'   🗑️  Deleted: {dup_file.name}')
            else:
                raise ArtificialDuplicateException(duplicate_report)

        # ===========================================
        # 9. WRITE PARQUET
        # ===========================================

        # Drop columns not in ImportTickSchema (e.g. legacy server_time)
        _PARQUET_COLUMNS = [
            'timestamp', 'time_msc', 'collected_msc',
            'bid', 'ask', 'last',
            'tick_volume', 'real_volume', 'chart_tick_volume',
            'spread_points', 'spread_pct', 'quote_age_ms', 'trade_id',
            'tick_flags', 'session',
        ]
        extra_cols = [c for c in df.columns if c not in _PARQUET_COLUMNS]
        if extra_cols:
            df = df.drop(columns=extra_cols)

        try:
            table = pa.Table.from_pandas(df)
            table = table.replace_schema_metadata(parquet_metadata)
            pq.write_table(table, parquet_path, compression='snappy')

            json_size = json_file.stat().st_size
            parquet_size = parquet_path.stat().st_size
            compression_ratio = json_size / parquet_size if parquet_size > 0 else 0

            if self._move_processed_files and self._finished_dir:
                self._finished_dir.mkdir(exist_ok=True)
                finished_file = self._finished_dir / json_file.name
                json_file.rename(finished_file)
                vLog.info(f'→ Moved {json_file.name} to finished/')

            self.total_ticks += len(df)
            offset_counts = self._applied_offsets.setdefault(broker_type_normalized, {})
            offset_counts[file_offset] = offset_counts.get(file_offset, 0) + 1

            time_suffix = ' (UTC)' if should_apply_offset else ''
            vLog.info(
                f'✅ {broker_type_normalized}/ticks/{symbol}/{parquet_name}: {len(df):,} Ticks{time_suffix}, '
                f'Compression {compression_ratio:.1f}:1 '
                f'({json_size/1024/1024:.1f}MB → {parquet_size/1024/1024:.1f}MB)'
            )

            vLog.debug(
                f'   market_type={market_type}, version={data_format_version}')

        except Exception as e:
            vLog.error(f'ERROR writing {parquet_path}')
            vLog.error(f'Original Error: {str(e)}')
            vLog.error(f'Error Type: {type(e)}')
            raise

    def _apply_server_clock(self, df: pd.DataFrame,
                            server_clock: ServerClockConfig) -> Tuple[pd.DataFrame, str]:
        """
        Convert the server wall-clock stamps of one file to UTC, tick by tick.

        `time_msc` carries the server's wall clock as epoch milliseconds; the rule resolves its
        UTC offset per stamp, so a file in US winter and one in summer get different offsets
        from the same rule. `timestamp` moves by the same per-tick amount, which keeps the two
        columns describing one moment. A file without `time_msc` is resolved from its
        `timestamp` alone.

        Args:
            df: Tick DataFrame with 'timestamp' (and 'time_msc') in server wall-clock time
            server_clock: The broker's server clock rule

        Returns:
            The converted DataFrame, and the distinct UTC offsets in hours it resolved to,
            comma-joined ('-2', or '-3,-2' across a season change)
        """
        if 'timestamp' not in df.columns or df.empty:
            return df, '0'

        has_time_msc = 'time_msc' in df.columns
        if has_time_msc:
            server_ms = df['time_msc'].to_numpy(dtype='int64')
        else:
            server_ms = df['timestamp'].to_numpy().astype('datetime64[ms]').astype('int64')
        utc_ms = server_clock_to_utc_ms(
            server_ms, server_clock.timezone, server_clock.hours_ahead)
        shift_ms = utc_ms - server_ms

        original_first = df['timestamp'].iloc[0]
        original_last = df['timestamp'].iloc[-1]

        if has_time_msc:
            df['time_msc'] = utc_ms
        df['timestamp'] = df['timestamp'] + pd.to_timedelta(shift_ms, unit='ms')

        offset_label = ','.join(
            f'{shift / 3_600_000:+g}' for shift in sorted(set(shift_ms.tolist())))

        vLog.info(f'   🕐 Server clock converted: {offset_label} hours')
        vLog.info(f'      Server: {original_first} → {original_last}')
        vLog.info(f"      UTC:    {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")

        return df, offset_label

    def _recalculate_sessions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Recalculates trading sessions based on UTC time.

        Args:
            df: DataFrame with 'timestamp' column (must be UTC after offset)

        Returns:
            DataFrame with corrected session labels
        """
        if 'session' in df.columns and 'timestamp' in df.columns:
            df['session'] = df['timestamp'].dt.hour.map(
                lambda h: get_session_from_utc_hour(h).value
            )
        return df

    def _check_for_existing_duplicate(
        self,
        source_json_name: str,
        broker_type: str,
        symbol: str,
        target_path: Path
    ) -> Optional[DuplicateReport]:
        """
        Check if Parquet file already exists with same source.

        Args:
            source_json_name: Name of source JSON file
            broker_type: Broker type identifier
            symbol: Trading symbol
            target_path: Target parquet path

        Returns:
            DuplicateReport if duplicate found, None otherwise
        """

        # OPTION 2: Cross-Collector Search (as documented)
        search_pattern = f'*/ticks/{symbol}/{symbol}_*.parquet'
        existing_files = list(self.target_dir.glob(search_pattern))

        if not existing_files:
            return None

        for existing_file in existing_files:
            try:
                parquet_file = pq.ParquetFile(existing_file)
                metadata_raw = parquet_file.metadata.metadata

                existing_metadata = {
                    key.decode('utf-8') if isinstance(key, bytes) else key:
                    value.decode('utf-8') if isinstance(value,
                                                        bytes) else value
                    for key, value in metadata_raw.items()
                }

                existing_source = existing_metadata.get('source_file', '')
                # legacy compability : data_collector
                existing_broker = existing_metadata.get(
                    'broker_type') or existing_metadata.get('data_collector', 'unknown')

                if existing_source == source_json_name:
                    relative_path = existing_file.relative_to(self.target_dir)
                    collector_path = relative_path.parts[0] if len(
                        relative_path.parts) > 0 else 'unknown'

                    vLog.warning(
                        f'⚠️  Found existing Parquet: {collector_path}/{symbol}/{existing_file.name}'
                    )
                    vLog.warning(
                        f"    Existing: broker_type='{existing_broker}' | "
                        f"Importing: broker_type='{broker_type}'"
                    )

                    existing_df = pd.read_parquet(existing_file)

                    return DuplicateReport(
                        source_file=source_json_name,
                        duplicate_files=[existing_file],
                        tick_counts=[len(existing_df)],
                        time_ranges=[
                            (existing_df['timestamp'].min(),
                             existing_df['timestamp'].max())
                        ],
                        file_sizes_mb=[
                            existing_file.stat().st_size / (1024 * 1024)],
                        metadata=[existing_metadata]
                    )

            except Exception as e:
                vLog.warning(
                    f'Could not read metadata from {existing_file.name}: {e}')

        return None

    def _optimize_datatypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimizes DataFrame datatypes for performance."""

        float_cols = ['bid', 'ask', 'last', 'spread_pct', 'real_volume']
        for col in float_cols:
            if col in df.columns:
                df[col] = df[col].astype('float32')

        int_cols = ['tick_volume', 'chart_tick_volume', 'spread_points']
        for col in int_cols:
            if col in df.columns:
                df[col] = df[col].astype('int32')

        # Millisecond epoch columns — int64 (too large for int32)
        int64_cols = ['time_msc', 'collected_msc']
        for col in int64_cols:
            if col in df.columns:
                df[col] = df[col].astype('int64')

        # Nullable by contract (collector format 1.6.0+): the age of the quote a
        # trade executed against, or null where no quote had been observed yet.
        # Pandas' capitalised Int32 is the one integer type that carries a null —
        # plain int32 cannot, and float64 would silently restate an integer age.
        nullable_int_cols = ['quote_age_ms']
        for col in nullable_int_cols:
            if col in df.columns:
                df[col] = df[col].astype('Int32')

        # The venue's per-pair trade id (collector 1.7.0+) — Int64 rather than Int32
        # because it is a counter that only grows, and null where the venue has none:
        # a quote-driven venue has no central place where trades happen, so absence is
        # correct rather than missing (§31c).
        nullable_int64_cols = ['trade_id']
        for col in nullable_int64_cols:
            if col in df.columns:
                df[col] = df[col].astype('Int64')

        return df

    def _print_summary(self):
        """Prints summary of batch processing."""

        vLog.info('\n' + '=' * 80)
        vLog.info('PROCESSING SUMMARY')
        vLog.info('=' * 80)
        vLog.info(f'✅ Processed files: {self.processed_files}')
        vLog.info(f'✅ Total ticks: {self.total_ticks:,}')
        for broker_type, offset_counts in self._applied_offsets.items():
            counts_str = ' · '.join(
                f'{offset}h × {count} file(s)' for offset, count in sorted(offset_counts.items()))
            vLog.info(f'✅ UTC offsets applied: {broker_type}: {counts_str}')
        vLog.info(f'⚠️  Warnings: {len(self.warnings)}')
        vLog.info(f'❌ Errors: {len(self.errors)}')

        if self.warnings:
            vLog.warning('\nWARNING LIST:')
            for warning in self.warnings:
                vLog.warning(f'  - {warning}')

        if self.errors:
            vLog.error('\nERROR LIST:')
            for error in self.errors:
                vLog.error(f'  - {error}')

        vLog.info('=' * 80 + '\n')

    def _trigger_bar_rendering(self):
        """
        Trigger automatic bar rendering after tick import.
        Renders bars for all broker_types that were processed.
        """
        vLog.info('\n' + '=' * 80)
        vLog.info('🔄 AUTO-TRIGGERING BAR RENDERING')
        vLog.info('=' * 80)

        try:
            bar_importer = BarImporter(data_dir=str(self.target_dir))

            bar_importer.render_bars_for_all_symbols(
                broker_types=list(self._processed_broker_types),
                clean_mode=True
            )
            bar_importer.update_bar_index()

            vLog.info('✅ Bar rendering completed!')

        except Exception as e:
            vLog.error(f'❌ Bar rendering failed: {e}')
            vLog.error('   You can manually trigger it later with:')
            vLog.error('   python python/cli/bar_index_cli.py render --all --clean')

    def _validate_broker_type(self, metadata: dict) -> str:
        """
        Validate broker_type exists and is mapped in market_config.json.

        Args:
            metadata: JSON metadata from source file

        Returns:
            Normalized broker_type string

        Raises:
            ValueError: If broker_type missing or not mapped
        """
        market_config = MarketConfigManager()
        available_brokers = market_config.get_all_broker_types()

        # Build available brokers string for error messages
        broker_list_str = '\n'.join(
            f'     • {bt} → {market_config.get_market_type(bt).value}'
            for bt in available_brokers
        )

        # Check 1: broker_type must exist in metadata
        broker_type = metadata.get('broker_type', None)

        # Check for legacy data_collector field
        if (broker_type is None):
            broker_type = metadata.get('data_collector', None)

        # if broker_type could not found at all:
        if broker_type is None:
            raise ValueError(
                f"Missing 'broker_type' (or LEGACY identifier data_collector) in JSON metadata.\n\n"
                f"   This appears to be a LEGACY file.\n\n"
                f"   To enable import, add the following to the JSON metadata section:\n"
                f"     \"broker_type\": ... "
                f"   Available broker_types in market_config.json:\n"
                f"{broker_list_str}"
            )

        # Normalize broker_type
        broker_type_normalized = self._normalize_broker_type(broker_type)

        # Check 2: broker_type must be mapped in market_config.json
        if broker_type_normalized not in available_brokers:
            raise ValueError(
                f"Unknown broker_type '{broker_type_normalized}'.\n\n"
                f"   Not found in configs/market_config.json.\n\n"
                f"   Available broker_types:\n"
                f"{broker_list_str}\n\n"
                f"   To add a new broker_type, update configs/market_config.json:\n"
                f"     {{\n"
                f"       \"broker_type\": \"{broker_type_normalized}\",\n"
                f"       \"market_type\": \"forex\",  // or \"crypto\"\n"
                f"       \"broker_config_path\": \"./configs/brokers/{broker_type_normalized}/config.json\"\n"
                f"     }}"
            )

        return broker_type_normalized
