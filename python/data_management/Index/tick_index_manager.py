"""
TickIndexManager - Fast File Selection via Metadata Index

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
from python.framework.reporting.coverage_report import (
    CoverageReport,
    IndexEntry
)

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.broker_types import BrokerType
vLog = get_global_logger()


class TickIndexManager:
    """
    Manages Parquet file index for fast time-based file selection.

    Storage: Parquet (flat table)
    Memory: Nested dict {broker_type: {symbol: [entries]}}

    Migration: Auto-converts legacy JSON index on first load.
    """

    # Index file names
    INDEX_FILE_PARQUET = ".parquet_tick_index.parquet"
    INDEX_FILE_JSON_LEGACY = ".parquet_tick_index.json"

    def __init__(self, logger: AbstractLogger = vLog):
        self.logger = logger
        self._app_config = AppConfigManager()
        self.data_dir = Path(self._app_config.get_data_processed_path())

        # NEW: Parquet index file
        self.index_file = self.data_dir / self.INDEX_FILE_PARQUET
        # Legacy JSON for migration
        self._legacy_json_file = self.data_dir / self.INDEX_FILE_JSON_LEGACY

        # {broker_type: {symbol: [files]}} - unchanged API
        self.index: Dict[str, Dict[str, List[Dict]]] = {}
        self.logger.info("📚 Parquet Tick Index Manager initialized.")

    # =========================================================================
    # INDEX BUILDING
    # =========================================================================

    def build_index(self, force_rebuild: bool = False, check_stale: bool = False) -> None:
        """
        Build or load index from Parquet files.

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
                    f"📚 Loaded existing tick index ({len(self.index)} broker types)")
                return

            if not self.needs_rebuild():
                self._load_index()
                self.logger.info(
                    f"📚 Loaded existing tick index ({len(self.index)} broker types)")
                return

        # Check for legacy JSON and migrate
        if not force_rebuild and self._legacy_json_file.exists() and not self.index_file.exists():
            self.logger.info("🔄 Migrating legacy JSON index to Parquet...")
            if self._migrate_from_json():
                self.logger.info("✅ Migration complete")
                return

        self.logger.info("🔍 Scanning Parquet files for tick index...")
        start_time = time.time()

        # Pattern: mt5/ticks/EURUSD/*.parquet
        parquet_files = list(self.data_dir.glob("*/ticks/**/*.parquet"))

        if not parquet_files:
            self.logger.warning(f"No Parquet files found in {self.data_dir}")
            self.index = {}
            return

        for parquet_file in parquet_files:
            try:
                entry = self._scan_file(parquet_file)
                broker_type = entry['broker_type']
                symbol = entry['symbol']

                if broker_type not in self.index:
                    self.index[broker_type] = {}

                if symbol not in self.index[broker_type]:
                    self.index[broker_type][symbol] = []

                self.index[broker_type][symbol].append(entry)

            except Exception as e:
                self.logger.warning(
                    f"Failed to index {parquet_file.name}: {e}")

        # Sort files chronologically per broker_type/symbol
        for broker_type in self.index:
            for symbol in self.index[broker_type]:
                self.index[broker_type][symbol].sort(
                    key=lambda x: x['start_time'])

        self._save_index()

        elapsed = time.time() - start_time
        total_files = sum(
            len(files)
            for bt in self.index.values()
            for files in bt.values()
        )
        self.logger.info(
            f"✅ Index built: {total_files} files across {len(self.index)} broker types "
            f"in {elapsed:.2f}s"
        )

    def _scan_file(self, parquet_file: Path) -> Dict:
        """
        Scan single Parquet file and extract metadata with statistics.
        """
        pq_file = pq.ParquetFile(parquet_file)

        try:
            symbol = parquet_file.name.split('_')[0]
        except IndexError:
            symbol = "UNKNOWN"

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
        file_size_mb = round(parquet_file.stat().st_size / (1024 * 1024), 2)

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
        return {
            'file': parquet_file.name,
            'path': str(parquet_file.absolute()),
            'symbol': symbol,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'tick_count': tick_count,
            'file_size_mb': file_size_mb,
            'source_file': source_file,
            'num_row_groups': pq_file.num_row_groups,

            'statistics': {
                'avg_spread_points': round(avg_spread_points, 2) if avg_spread_points else None,
                'avg_spread_pct': round(avg_spread_pct, 6) if avg_spread_pct else None,
                'tick_frequency_per_second': tick_frequency
            },

            'sessions': {str(k): int(v) for k, v in sessions.items()},

            'broker_type': broker_type
        }

    def needs_rebuild(self) -> bool:
        """Check if index needs rebuilding."""
        if not self.index_file.exists():
            return True

        index_mtime = self.index_file.stat().st_mtime

        parquet_files = list(self.data_dir.glob("*/ticks/**/*.parquet"))
        if parquet_files:
            newest_parquet = max(f.stat().st_mtime for f in parquet_files)

            if newest_parquet > index_mtime:
                self.logger.info(
                    "📋 Tick index outdated - newer Parquet files found")
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

        relevant = []

        for entry in self.index[broker_type][symbol]:
            file_start = pd.to_datetime(entry['start_time'], utc=True)
            file_end = pd.to_datetime(entry['end_time'], utc=True)

            if file_start <= end_date and file_end >= start_date:
                relevant.append(Path(entry['path']))

        return relevant

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
                        'start_time': pd.to_datetime(entry['start_time']),
                        'end_time': pd.to_datetime(entry['end_time']),
                        'tick_count': entry['tick_count'],
                        'file_size_mb': entry['file_size_mb'],
                        'source_file': entry['source_file'],
                        'num_row_groups': entry['num_row_groups'],
                        # Nested dicts as JSON strings
                        'statistics': json.dumps(entry.get('statistics', {})),
                        'sessions': json.dumps(entry.get('sessions', {})),
                    }
                    rows.append(row)

        if not rows:
            # Empty index - create empty parquet with schema
            df = pd.DataFrame(columns=[
                'broker_type', 'symbol', 'file', 'path', 'start_time', 'end_time',
                'tick_count', 'file_size_mb', 'source_file', 'num_row_groups',
                'statistics', 'sessions'
            ])
        else:
            df = pd.DataFrame(rows)

        # Add metadata
        metadata = {
            b'created_at': datetime.now(timezone.utc).isoformat().encode(),
            b'data_dir': str(self.data_dir).encode(),
            b'index_version': b'2.0'  # Parquet format version
        }

        table = pa.Table.from_pandas(df)
        table = table.replace_schema_metadata(
            {**table.schema.metadata, **metadata})

        pq.write_table(table, self.index_file)
        self.logger.debug(f"💾 Tick index saved to {self.index_file}")

    def _load_index(self) -> None:
        """Load index from Parquet file and convert to nested dict."""
        try:
            df = pd.read_parquet(self.index_file)
            self.index = self._dataframe_to_nested_dict(df)
        except Exception as e:
            self.logger.warning(f"Failed to load tick index: {e}")
            self.index = {}

    def _dataframe_to_nested_dict(self, df: pd.DataFrame) -> Dict[str, Dict[str, List[Dict]]]:
        """Convert flat DataFrame to nested dict structure."""
        result = {}

        for _, row in df.iterrows():
            broker_type = row['broker_type']
            symbol = row['symbol']

            if broker_type not in result:
                result[broker_type] = {}

            if symbol not in result[broker_type]:
                result[broker_type][symbol] = []

            entry = {
                'file': row['file'],
                'path': row['path'],
                'symbol': symbol,
                'start_time': row['start_time'].isoformat() if pd.notna(row['start_time']) else None,
                'end_time': row['end_time'].isoformat() if pd.notna(row['end_time']) else None,
                'tick_count': int(row['tick_count']),
                'file_size_mb': float(row['file_size_mb']),
                'source_file': row['source_file'],
                'num_row_groups': int(row['num_row_groups']),
                'statistics': json.loads(row['statistics']) if row['statistics'] else {},
                'sessions': json.loads(row['sessions']) if row['sessions'] else {},
                'broker_type': broker_type
            }

            result[broker_type][symbol].append(entry)

        # Sort by start_time
        for broker_type in result:
            for symbol in result[broker_type]:
                result[broker_type][symbol].sort(
                    key=lambda x: x['start_time'] or '')

        return result

    def _migrate_from_json(self) -> bool:
        """Migrate from legacy JSON format to Parquet."""
        try:
            with open(self._legacy_json_file, 'r') as f:
                data = json.load(f)
                self.index = data.get('symbols', {})

            self._save_index()

            # Optionally rename old file
            backup_path = self._legacy_json_file.with_suffix('.json.bak')
            self._legacy_json_file.rename(backup_path)
            self.logger.info(f"📦 Legacy JSON backed up to {backup_path}")

            return True
        except Exception as e:
            self.logger.error(f"Migration failed: {e}")
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
    # COVERAGE REPORTS
    # =========================================================================

    def get_coverage_report(self, broker_type: BrokerType, symbol: str) -> CoverageReport:
        """Generate coverage report for a symbol."""
        if broker_type not in self.index:
            self.logger.warning(
                f"Broker type '{broker_type}' not found in tick index")
            return None

        if symbol not in self.index[broker_type]:
            self.logger.warning(
                f"Symbol '{symbol}' not found in tick index for broker_type '{broker_type}'")
            return None

        report = CoverageReport(
            symbol, broker_type=broker_type)
        report.analyze()
        return report

    def get_symbol_coverage(self, broker_type: str, symbol: str) -> Dict:
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
        print("\n" + "="*60)
        print("📚 Parquet Tick Index Summary")
        print("="*60)

        if not self.index:
            print("   (empty tick index)")
            return

        for broker_type in sorted(self.index.keys()):
            print(f"\n📂 {broker_type}:")

            for symbol in sorted(self.index[broker_type].keys()):
                coverage = self.get_symbol_coverage(broker_type, symbol)
                print(f"   {symbol}:")
                print(f"      Files:  {coverage['num_files']}")
                print(f"      Ticks:  {coverage['total_ticks']:,}")
                print(f"      Size:   {coverage['total_size_mb']:.1f} MB")
                print(
                    f"      Range:  {coverage['start_time'][:10]} → {coverage['end_time'][:10]}")

        print("="*60 + "\n")

    def print_coverage_report(self, broker_type: BrokerType, symbol: str) -> None:
        """Print coverage report for a symbol."""
        report = self.get_coverage_report(broker_type, symbol)
        if report is not None:
            print(report.generate_report())
