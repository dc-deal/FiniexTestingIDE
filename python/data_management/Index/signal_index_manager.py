"""
SignalIndexManager - Fast signal-parquet selection via a metadata index (#429).

Mirrors TickIndexManager for the signal data source: one processed parquet per source file
covers all symbols (row per (collected_msc, symbol) + an envelope sentinel row), so a scanned
file is registered under EACH real symbol it carries. Keyed by data_sentiment_type
(= the archive's pipeline_id) instead of broker_type.

Storage: Parquet (flat table). Memory: {data_sentiment_type: {symbol: [entries]}}.
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL, SignalParquetColumn)

vLog = get_global_logger()


class SignalIndexManager:
    """
    Manages the signal parquet index for fast time-based file selection.

    Storage: Parquet (flat table)
    Memory: Nested dict {data_sentiment_type: {symbol: [entries]}}
    """

    INDEX_FILE_PARQUET = ".signal_index.parquet"

    def __init__(self, logger: AbstractLogger = vLog, data_dir: Optional[str] = None):
        """
        Args:
            logger: Logger instance
            data_dir: Signal parquet root (default: <data_processed>/signals)
        """
        self.logger = logger
        self._app_config = AppConfigManager()
        self.data_dir = Path(data_dir) if data_dir else (
            Path(self._app_config.get_data_processed_path()) / 'signals')

        self.index_file = self.data_dir / self.INDEX_FILE_PARQUET

        # {data_sentiment_type: {symbol: [entries]}}
        self.index: Dict[str, Dict[str, List[Dict]]] = {}
        self.logger.info("📡 Signal Index Manager initialized.")

    # =========================================================================
    # INDEX BUILDING
    # =========================================================================

    def build_index(self, force_rebuild: bool = False, check_stale: bool = False) -> None:
        """
        Build or load the index from the signal parquet files.

        Args:
            force_rebuild: Force complete rebuild, ignore an existing index
            check_stale: Rebuild only if newer parquet files exist (filesystem scan)
        """
        if not force_rebuild and self.index_file.exists():
            if not check_stale:
                self._load_index()
                self.logger.info(
                    f"📡 Loaded existing signal index ({len(self.index)} sources)")
                return
            if not self.needs_rebuild():
                self._load_index()
                self.logger.info(
                    f"📡 Loaded existing signal index ({len(self.index)} sources)")
                return

        self.logger.info("🔍 Scanning signal parquet files...")
        start_time = time.time()

        parquet_files = self._parquet_files()
        if not parquet_files:
            self.logger.warning(f"No signal parquet found in {self.data_dir}")
            self.index = {}
            return

        for parquet_file in parquet_files:
            try:
                base = self._scan_file(parquet_file)
                sentiment_type = base['data_sentiment_type']
                symbols = base.pop('symbols')

                for symbol in symbols:
                    entry = {**base, 'symbol': symbol}
                    self.index.setdefault(sentiment_type, {}).setdefault(
                        symbol, []).append(entry)
            except Exception as e:
                self.logger.warning(
                    f"Failed to index {parquet_file.name}: {e}")

        for sentiment_type in self.index:
            for symbol in self.index[sentiment_type]:
                self.index[sentiment_type][symbol].sort(
                    key=lambda x: x['start_time'])

        self._save_index()

        elapsed = time.time() - start_time
        total_files = sum(
            len(files)
            for st in self.index.values()
            for files in st.values()
        )
        self.logger.info(
            f"✅ Signal index built: {total_files} file-entries across "
            f"{len(self.index)} sources in {elapsed:.2f}s"
        )

    def _parquet_files(self) -> List[Path]:
        """Signal parquet files (<pipeline_id>/*.parquet — excludes the index file at root)."""
        return sorted(self.data_dir.glob("*/*.parquet"))

    def _scan_file(self, parquet_file: Path) -> Dict:
        """
        Scan one signal parquet: its data_sentiment_type, real symbols, and collected_msc
        range. The whole-file range is used per symbol — the envelope sentinel rows keep
        every symbol resolvable across the full window.
        """
        cols = [
            SignalParquetColumn.COLLECTED_MSC.value,
            SignalParquetColumn.SYMBOL.value,
            SignalParquetColumn.PIPELINE_ID.value,
        ]
        df = pd.read_parquet(parquet_file, columns=cols)

        pipeline_col = df[SignalParquetColumn.PIPELINE_ID.value]
        sentiment_type = str(pipeline_col.iloc[0]) if len(
            pipeline_col) else parquet_file.parent.name

        msc = df[SignalParquetColumn.COLLECTED_MSC.value]
        start_time = datetime.fromtimestamp(int(msc.min()) / 1000.0, tz=timezone.utc)
        end_time = datetime.fromtimestamp(int(msc.max()) / 1000.0, tz=timezone.utc)

        symbols = sorted(
            set(df[SignalParquetColumn.SYMBOL.value].unique()) - {SIGNAL_ENVELOPE_SYMBOL})

        file_size_mb = round(parquet_file.stat().st_size / (1024 * 1024), 4)

        return {
            'file': parquet_file.name,
            'path': str(parquet_file.absolute()),
            'data_sentiment_type': sentiment_type,
            'symbols': symbols,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'row_count': int(len(df)),
            'file_size_mb': file_size_mb,
        }

    def needs_rebuild(self) -> bool:
        """True if any signal parquet is newer than the index."""
        if not self.index_file.exists():
            return True

        index_mtime = self.index_file.stat().st_mtime
        parquet_files = self._parquet_files()
        if parquet_files:
            newest_parquet = max(f.stat().st_mtime for f in parquet_files)
            if newest_parquet > index_mtime:
                self.logger.info(
                    "📋 Signal index outdated - newer parquet files found")
                return True
        return False

    # =========================================================================
    # FILE SELECTION
    # =========================================================================

    def get_relevant_files(
        self,
        data_sentiment_type: str,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Path]:
        """
        Find the signal parquet files covering [start_date, end_date] for one
        (data_sentiment_type, symbol).

        Args:
            data_sentiment_type: Source identity (= pipeline_id)
            symbol: Trading symbol
            start_date: Range start (UTC)
            end_date: Range end (UTC)

        Returns:
            Overlapping parquet paths (empty if the source/symbol is unknown)
        """
        if data_sentiment_type not in self.index:
            self.logger.warning(
                f"Sentiment source '{data_sentiment_type}' not found in signal index")
            return []

        if symbol not in self.index[data_sentiment_type]:
            self.logger.warning(
                f"Symbol '{symbol}' not found in signal index for "
                f"source '{data_sentiment_type}'")
            return []

        relevant = []
        for entry in self.index[data_sentiment_type][symbol]:
            file_start = pd.to_datetime(entry['start_time'], utc=True)
            file_end = pd.to_datetime(entry['end_time'], utc=True)
            if file_start <= end_date and file_end >= start_date:
                relevant.append(Path(entry['path']))
        return relevant

    # =========================================================================
    # INDEX PERSISTENCE - PARQUET FORMAT
    # =========================================================================

    def _save_index(self) -> None:
        """Save the index to a flat parquet table."""
        rows = []
        for sentiment_type, symbols in self.index.items():
            for symbol, entries in symbols.items():
                for entry in entries:
                    rows.append({
                        'data_sentiment_type': sentiment_type,
                        'symbol': symbol,
                        'file': entry['file'],
                        'path': entry['path'],
                        'start_time': pd.to_datetime(entry['start_time']),
                        'end_time': pd.to_datetime(entry['end_time']),
                        'row_count': entry['row_count'],
                        'file_size_mb': entry['file_size_mb'],
                    })

        columns = [
            'data_sentiment_type', 'symbol', 'file', 'path',
            'start_time', 'end_time', 'row_count', 'file_size_mb',
        ]
        df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)

        metadata = {
            b'created_at': datetime.now(timezone.utc).isoformat().encode(),
            b'data_dir': str(self.data_dir).encode(),
            b'index_version': b'1.0',
        }
        table = pa.Table.from_pandas(df)
        table = table.replace_schema_metadata({**table.schema.metadata, **metadata})
        pq.write_table(table, self.index_file)
        self.logger.debug(f"💾 Signal index saved to {self.index_file}")

    def _load_index(self) -> None:
        """Load the index from parquet into the nested dict."""
        try:
            df = pd.read_parquet(self.index_file)
            self.index = self._dataframe_to_nested_dict(df)
        except Exception as e:
            self.logger.warning(f"Failed to load signal index: {e}")
            self.index = {}

    def _dataframe_to_nested_dict(self, df: pd.DataFrame) -> Dict[str, Dict[str, List[Dict]]]:
        """Convert the flat index DataFrame to the nested dict structure."""
        result: Dict[str, Dict[str, List[Dict]]] = {}
        for _, row in df.iterrows():
            sentiment_type = row['data_sentiment_type']
            symbol = row['symbol']
            entry = {
                'file': row['file'],
                'path': row['path'],
                'symbol': symbol,
                'data_sentiment_type': sentiment_type,
                'start_time': row['start_time'].isoformat() if pd.notna(row['start_time']) else None,
                'end_time': row['end_time'].isoformat() if pd.notna(row['end_time']) else None,
                'row_count': int(row['row_count']),
                'file_size_mb': float(row['file_size_mb']),
            }
            result.setdefault(sentiment_type, {}).setdefault(symbol, []).append(entry)

        for sentiment_type in result:
            for symbol in result[sentiment_type]:
                result[sentiment_type][symbol].sort(
                    key=lambda x: x['start_time'] or '')
        return result

    # =========================================================================
    # COVERAGE + UTILITY
    # =========================================================================

    def get_symbol_file_coverage(self, data_sentiment_type: str, symbol: str) -> Dict:
        """Coverage statistics for one (data_sentiment_type, symbol)."""
        if data_sentiment_type not in self.index:
            return {}
        if symbol not in self.index[data_sentiment_type]:
            return {}

        entries = self.index[data_sentiment_type][symbol]
        return {
            'num_files': len(entries),
            'total_rows': sum(e['row_count'] for e in entries),
            'total_size_mb': round(sum(e['file_size_mb'] for e in entries), 4),
            'start_time': entries[0]['start_time'],
            'end_time': entries[-1]['end_time'],
            'files': [e['file'] for e in entries],
        }

    def list_symbols(self, data_sentiment_type: Optional[str] = None) -> List[str]:
        """List available symbols (optionally for one source)."""
        if data_sentiment_type:
            if data_sentiment_type not in self.index:
                return []
            return sorted(self.index[data_sentiment_type].keys())

        all_symbols = set()
        for st in self.index:
            all_symbols.update(self.index[st].keys())
        return sorted(all_symbols)

    def list_sentiment_types(self) -> List[str]:
        """List available signal sources (data_sentiment_type keys)."""
        return sorted(self.index.keys())

    def print_summary(self) -> None:
        """Print an index summary grouped by data_sentiment_type."""
        print("\n" + "=" * 60)
        print("📡 Signal Index Summary")
        print("=" * 60)

        if not self.index:
            print("   (empty signal index)")
            return

        for sentiment_type in sorted(self.index.keys()):
            print(f"\n📂 {sentiment_type}:")
            for symbol in sorted(self.index[sentiment_type].keys()):
                coverage = self.get_symbol_file_coverage(sentiment_type, symbol)
                print(f"   {symbol}:")
                print(f"      Files:  {coverage['num_files']}")
                print(f"      Rows:   {coverage['total_rows']:,}")
                print(f"      Range:  {coverage['start_time'][:10]} → {coverage['end_time'][:10]}")

        print("=" * 60 + "\n")
