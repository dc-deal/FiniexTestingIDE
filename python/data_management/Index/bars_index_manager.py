"""
Parquet Bars Index Manager - Fast Bar File Selection
====================================================

Manages index for pre-rendered bar files.
Enables O(1) file selection for warmup and backtesting.

REFACTORED: Parquet storage format (was JSON)
- Flat table structure for efficient filtering
- Nested dict in memory for API compatibility
- Auto-migration from legacy JSON format
"""

import json
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
vLog = get_global_logger()


class BarsIndexManager:
    """
    Manages index for pre-rendered bar parquet files.

    Storage: Parquet (flat table)
    Memory: Nested dict {broker_type: {symbol: {timeframe: entry}}}

    Migration: Auto-converts legacy JSON index on first load.
    """

    # Index file names
    INDEX_FILE_PARQUET = ".parquet_bars_index.parquet"
    INDEX_FILE_JSON_LEGACY = ".parquet_bars_index.json"

    def __init__(self, logger: AbstractLogger = vLog):
        """Initialize bar index manager."""
        self._app_config = AppConfigManager()
        self.data_dir = Path(self._app_config.get_data_processed_path())

        # NEW: Parquet index file
        self.index_file = self.data_dir / self.INDEX_FILE_PARQUET
        # Legacy JSON for migration
        self._legacy_json_file = self.data_dir / self.INDEX_FILE_JSON_LEGACY

        # {broker_type: {symbol: {timeframe: entry}}} - unchanged API
        self.index: Dict[str, Dict[str, Dict[str, Dict]]] = {}
        self.logger = logger

    def _version_less_than(self, version: str, compare_to: str) -> bool:
        """
        Compare version strings (e.g., '1.0.5' < '1.1.0').
        """
        try:
            v1 = [int(x) for x in version.split('.')]
            v2 = [int(x) for x in compare_to.split('.')]
            while len(v1) < 3:
                v1.append(0)
            while len(v2) < 3:
                v2.append(0)
            return tuple(v1) < tuple(v2)
        except (ValueError, AttributeError):
            return True

    # =========================================================================
    # INDEX BUILDING
    # =========================================================================

    def build_index(self, force_rebuild: bool = False, check_stale: bool = False) -> None:
        """
        Build or load index from bar parquet files.

        Args:
            force_rebuild: Force complete rebuild, ignore existing index
            check_stale: Check if index is outdated (expensive filesystem scan)
                        Default False - assumes index is current
        """
        # Fast path: Load existing index without checking staleness
        if not force_rebuild and self.index_file.exists():
            if not check_stale:
                self._load_index()
                self.logger.info(
                    f"📚 Loaded existing bar index ({self._count_symbols()} symbols)")
                return

            if not self.needs_rebuild():
                self._load_index()
                self.logger.info(
                    f"📚 Loaded existing bar index ({self._count_symbols()} symbols)")
                return

        # Check for legacy JSON and migrate
        if not force_rebuild and self._legacy_json_file.exists() and not self.index_file.exists():
            self.logger.info("🔄 Migrating legacy JSON bar index to Parquet...")
            if self._migrate_from_json():
                self.logger.info("✅ Migration complete")
                return

        self.logger.info("🔍 Scanning bar files for index...")
        start_time = time.time()

        # Scan pattern: */bars/**/*.parquet
        bar_files = list(self.data_dir.glob("*/bars/**/*_BARS.parquet"))

        if not bar_files:
            self.logger.warning(f"No bar files found in {self.data_dir}")
            self.index = {}
            return

        # Process each file
        for bar_file in bar_files:
            try:
                entry = self._scan_bar_file(bar_file)
                broker_type = entry['broker_type']
                symbol = entry['symbol']
                timeframe = entry['timeframe']

                if broker_type not in self.index:
                    self.index[broker_type] = {}

                if symbol not in self.index[broker_type]:
                    self.index[broker_type][symbol] = {}

                self.index[broker_type][symbol][timeframe] = entry

            except Exception as e:
                self.logger.warning(
                    f"Failed to index bar file {bar_file.name}: {e}")

        self._save_index()

        elapsed = time.time() - start_time
        total_entries = sum(
            len(tfs)
            for symbols in self.index.values()
            for tfs in symbols.values()
        )
        self.logger.info(
            f"✅ Bar index built: {total_entries} timeframes across "
            f"{self._count_symbols()} symbols in {elapsed:.2f}s"
        )

    def _count_symbols(self) -> int:
        """Count total unique symbols across all broker types."""
        symbols = set()
        for broker_type in self.index:
            symbols.update(self.index[broker_type].keys())
        return len(symbols)

    def _scan_bar_file(self, bar_file: Path) -> Dict:
        """
        Scan single bar parquet file and extract metadata.
        """
        pq_file = pq.ParquetFile(bar_file)

        # Extract metadata
        custom_metadata = pq_file.metadata.metadata

        # Parse metadata (bytes → str)
        metadata = {
            key.decode('utf-8') if isinstance(key, bytes) else key:
            value.decode('utf-8') if isinstance(value, bytes) else value
            for key, value in custom_metadata.items()
        }

        symbol = metadata.get('symbol', 'UNKNOWN')
        timeframe = metadata.get('timeframe', 'UNKNOWN')

        # Read first and last bars for time range
        first_bar = pq_file.read_row_group(0, columns=['timestamp'])
        start_time = first_bar['timestamp'][0].as_py()

        last_row_group_idx = pq_file.num_row_groups - 1
        last_bar = pq_file.read_row_group(
            last_row_group_idx,
            columns=['timestamp']
        )
        end_time = last_bar['timestamp'][-1].as_py()

        # === Load full DataFrame for aggregations ===
        df = pd.read_parquet(bar_file)

        # Tick count statistics
        total_tick_count = int(df['tick_count'].sum())
        avg_ticks_per_bar = float(
            df['tick_count'].mean()) if len(df) > 0 else 0.0
        min_ticks_per_bar = int(df['tick_count'].min()) if len(df) > 0 else 0
        max_ticks_per_bar = int(df['tick_count'].max()) if len(df) > 0 else 0

        # Bar type distribution
        real_bar_count = int((df['bar_type'] == 'real').sum())
        synthetic_bar_count = int((df['bar_type'] == 'synthetic').sum())

        # Volume statistics
        if 'volume' in df.columns:
            total_trade_volume = float(df['volume'].sum())
            avg_volume_per_bar = float(
                df['volume'].mean()) if len(df) > 0 else 0.0
        else:
            total_trade_volume = None
            avg_volume_per_bar = None

        # Version and market type detection
        source_version_min = metadata.get('source_version_min', '1.0.0')
        source_version_max = metadata.get('source_version_max', '1.0.0')

        return {
            'file': bar_file.name,
            'path': str(bar_file.absolute()),
            'symbol': symbol,
            'timeframe': timeframe,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'bar_count': pq_file.metadata.num_rows,
            'file_size_mb': round(bar_file.stat().st_size / (1024 * 1024), 2),
            'num_row_groups': pq_file.num_row_groups,
            'rendered_at': metadata.get('rendered_at', 'unknown'),
            'total_tick_count': total_tick_count,
            'avg_ticks_per_bar': round(avg_ticks_per_bar, 2),
            'min_ticks_per_bar': min_ticks_per_bar,
            'max_ticks_per_bar': max_ticks_per_bar,
            'real_bar_count': real_bar_count,
            'synthetic_bar_count': synthetic_bar_count,
            'source_version_min': source_version_min,
            'source_version_max': source_version_max,
            'broker_type': metadata.get('broker_type') or metadata.get('data_collector', 'mt5'),
            'total_trade_volume': round(total_trade_volume, 6) if total_trade_volume is not None else None,
            'avg_volume_per_bar': round(avg_volume_per_bar, 6) if avg_volume_per_bar is not None else None,
        }

    def needs_rebuild(self) -> bool:
        """Check if index needs rebuilding."""
        if not self.index_file.exists():
            return True

        index_mtime = self.index_file.stat().st_mtime

        bar_files = list(self.data_dir.glob("*/bars/**/*_BARS.parquet"))
        if bar_files:
            newest_bar = max(f.stat().st_mtime for f in bar_files)

            if newest_bar > index_mtime:
                self.logger.info(
                    "📋 Bar index outdated - newer bar files found")
                return True

        return False

    # =========================================================================
    # FILE SELECTION
    # =========================================================================

    def get_bar_file(
        self,
        broker_type: str,
        symbol: str,
        timeframe: str
    ) -> Optional[Path]:
        """
        Get bar file path for broker_type/symbol/timeframe.
        """
        if broker_type not in self.index:
            self.logger.warning(
                f"Broker type '{broker_type}' not found in bar index")
            return None

        if symbol not in self.index[broker_type]:
            self.logger.warning(
                f"Symbol '{symbol}' not found in bar index for broker_type '{broker_type}'")
            return None

        if timeframe not in self.index[broker_type][symbol]:
            self.logger.warning(
                f"Timeframe '{timeframe}' not found for {symbol} in bar index (broker_type: {broker_type})"
            )
            return None

        entry = self.index[broker_type][symbol][timeframe]
        return Path(entry['path'])

    def get_available_timeframes(self, broker_type: str, symbol: str) -> List[str]:
        """Get list of available timeframes for a symbol."""
        if broker_type not in self.index:
            return []

        if symbol not in self.index[broker_type]:
            return []

        return sorted(self.index[broker_type][symbol].keys())

    # =========================================================================
    # INDEX PERSISTENCE - PARQUET FORMAT
    # =========================================================================

    def _save_index(self) -> None:
        """Save index to Parquet file (flat structure)."""
        rows = []

        for broker_type, symbols in self.index.items():
            for symbol, timeframes in symbols.items():
                for timeframe, entry in timeframes.items():
                    row = {
                        'broker_type': broker_type,
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'file': entry['file'],
                        'path': entry['path'],
                        'start_time': pd.to_datetime(entry['start_time']),
                        'end_time': pd.to_datetime(entry['end_time']),
                        'bar_count': entry['bar_count'],
                        'file_size_mb': entry['file_size_mb'],
                        'num_row_groups': entry['num_row_groups'],
                        'rendered_at': entry.get('rendered_at', ''),
                        'total_tick_count': entry.get('total_tick_count', 0),
                        'avg_ticks_per_bar': entry.get('avg_ticks_per_bar', 0.0),
                        'min_ticks_per_bar': entry.get('min_ticks_per_bar', 0),
                        'max_ticks_per_bar': entry.get('max_ticks_per_bar', 0),
                        'real_bar_count': entry.get('real_bar_count', 0),
                        'synthetic_bar_count': entry.get('synthetic_bar_count', 0),
                        'source_version_min': entry.get('source_version_min', ''),
                        'source_version_max': entry.get('source_version_max', ''),
                        'total_trade_volume': entry.get('total_trade_volume'),
                        'avg_volume_per_bar': entry.get('avg_volume_per_bar'),
                    }
                    rows.append(row)

        if not rows:
            df = pd.DataFrame(columns=[
                'broker_type', 'symbol', 'timeframe', 'file', 'path',
                'start_time', 'end_time', 'bar_count', 'file_size_mb',
                'num_row_groups', 'rendered_at', 'total_tick_count',
                'avg_ticks_per_bar', 'min_ticks_per_bar', 'max_ticks_per_bar',
                'real_bar_count', 'synthetic_bar_count', 'source_version_min',
                'source_version_max', 'total_trade_volume', 'avg_volume_per_bar'
            ])
        else:
            df = pd.DataFrame(rows)

        # Add metadata
        metadata = {
            b'created_at': datetime.now(timezone.utc).isoformat().encode(),
            b'data_dir': str(self.data_dir).encode(),
            b'index_version': b'2.0'
        }

        table = pa.Table.from_pandas(df)
        table = table.replace_schema_metadata(
            {**table.schema.metadata, **metadata})

        pq.write_table(table, self.index_file)
        self.logger.debug(f"💾 Bar index saved to {self.index_file}")

    def _load_index(self) -> None:
        """Load index from Parquet file and convert to nested dict."""
        try:
            df = pd.read_parquet(self.index_file)
            self.index = self._dataframe_to_nested_dict(df)
        except Exception as e:
            self.logger.warning(f"Failed to load bar index: {e}")
            self.index = {}

    def _dataframe_to_nested_dict(self, df: pd.DataFrame) -> Dict[str, Dict[str, Dict[str, Dict]]]:
        """Convert flat DataFrame to nested dict structure."""
        result = {}

        for _, row in df.iterrows():
            broker_type = row['broker_type']
            symbol = row['symbol']
            timeframe = row['timeframe']

            if broker_type not in result:
                result[broker_type] = {}

            if symbol not in result[broker_type]:
                result[broker_type][symbol] = {}

            entry = {
                'file': row['file'],
                'path': row['path'],
                'symbol': symbol,
                'timeframe': timeframe,
                'start_time': row['start_time'].isoformat() if pd.notna(row['start_time']) else None,
                'end_time': row['end_time'].isoformat() if pd.notna(row['end_time']) else None,
                'bar_count': int(row['bar_count']),
                'file_size_mb': float(row['file_size_mb']),
                'num_row_groups': int(row['num_row_groups']),
                'rendered_at': row.get('rendered_at', ''),
                'total_tick_count': int(row['total_tick_count']) if pd.notna(row.get('total_tick_count')) else 0,
                'avg_ticks_per_bar': float(row['avg_ticks_per_bar']) if pd.notna(row.get('avg_ticks_per_bar')) else 0.0,
                'min_ticks_per_bar': int(row['min_ticks_per_bar']) if pd.notna(row.get('min_ticks_per_bar')) else 0,
                'max_ticks_per_bar': int(row['max_ticks_per_bar']) if pd.notna(row.get('max_ticks_per_bar')) else 0,
                'real_bar_count': int(row['real_bar_count']) if pd.notna(row.get('real_bar_count')) else 0,
                'synthetic_bar_count': int(row['synthetic_bar_count']) if pd.notna(row.get('synthetic_bar_count')) else 0,
                'source_version_min': row.get('source_version_min', ''),
                'source_version_max': row.get('source_version_max', ''),
                'broker_type': broker_type,
                'total_trade_volume': float(row['total_trade_volume']) if pd.notna(row.get('total_trade_volume')) else None,
                'avg_volume_per_bar': float(row['avg_volume_per_bar']) if pd.notna(row.get('avg_volume_per_bar')) else None,
            }

            result[broker_type][symbol][timeframe] = entry

        return result

    def _migrate_from_json(self) -> bool:
        """Migrate from legacy JSON format to Parquet."""
        try:
            with open(self._legacy_json_file, 'r') as f:
                data = json.load(f)
                self.index = data.get('symbols', {})

            self._save_index()

            backup_path = self._legacy_json_file.with_suffix('.json.bak')
            self._legacy_json_file.rename(backup_path)
            self.logger.info(f"📦 Legacy JSON backed up to {backup_path}")

            return True
        except Exception as e:
            self.logger.error(f"Migration failed: {e}")
            return False

    # =========================================================================
    # LEGACY COMPATIBILITY
    # =========================================================================

    def save_index(self) -> None:
        """Public method for saving index (backwards compatible)."""
        self._save_index()

    def load_index(self) -> None:
        """Public method for loading index (backwards compatible)."""
        self._load_index()

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def list_symbols(self, broker_type: Optional[str] = None) -> List[str]:
        """List all available symbols in bar index."""
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

    def get_symbol_stats(self, broker_type: str, symbol: str) -> Dict:
        """Get statistics for a symbol."""
        if broker_type not in self.index:
            return {}

        if symbol not in self.index[broker_type]:
            return {}

        stats = {}
        for timeframe, entry in self.index[broker_type][symbol].items():
            stats[timeframe] = {
                'bar_count': entry['bar_count'],
                'file_size_mb': entry['file_size_mb'],
                'start_time': entry['start_time'],
                'end_time': entry['end_time']
            }

        return stats

    def print_summary(self) -> None:
        """Print bar index summary grouped by broker_type."""
        print("\n" + "="*60)
        print("📊 Bar Index Summary")
        print("="*60)

        if not self.index:
            print("   (empty bar index)")
            return

        for broker_type in sorted(self.index.keys()):
            print(f"\n📂 {broker_type}:")

            for symbol in sorted(self.index[broker_type].keys()):
                print(f"   {symbol}:")
                stats = self.get_symbol_stats(broker_type, symbol)

                for timeframe in sorted(stats.keys()):
                    tf_stats = stats[timeframe]
                    print(f"      {timeframe}: {tf_stats['bar_count']:,} bars "
                          f"({tf_stats['file_size_mb']:.1f} MB)")

        print("="*60 + "\n")
