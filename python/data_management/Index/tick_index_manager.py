"""
TickIndexManager - Fast File Selection via Metadata Index
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
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
    """

    def __init__(self, logger: AbstractLogger = vLog):
        self.logger = logger
        self._app_config = AppConfigManager()
        self.data_dir = Path(self._app_config.get_data_processed_path())
        # CHANGED: Index-Dateiname für Ticks
        self.index_file = self.data_dir / ".parquet_tick_index.json"
        # {broker_type: {symbol: [files]}}
        self.index: Dict[str, Dict[str, List[Dict]]] = {}
        self.logger.info("📚 Parquet Index Manager initialized.")

    # =========================================================================
    # INDEX BUILDING - ANGEPASST
    # =========================================================================

    def build_index(self, force_rebuild: bool = False) -> None:
        """
        Build or load index from Parquet files.
        """
        if not force_rebuild and not self.needs_rebuild():
            self.load_index()
            self.logger.info(
                f"📚 Loaded existing index ({len(self.index)} symbols)")
            return

        self.logger.info("🔍 Scanning Parquet files for index...")
        start_time = time.time()

        # CHANGED: Scanne nur Tick-Files
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

                # Initialize nested structure
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

        self.save_index()

        elapsed = time.time() - start_time
        total_files = sum(len(files) for files in self.index.values())
        self.logger.info(
            f"✅ Index built: {total_files} files across {len(self.index)} symbols "
            f"in {elapsed:.2f}s"
        )

    def _scan_file(self, parquet_file: Path) -> Dict:
        """
        Scan single Parquet file and extract metadata with statistics.

        Extended to calculate:
        - Spread statistics (avg points, avg pct)
        - Tick frequency (ticks per second)
        - Session distribution
        - Market type and data source

        Uses sampling for large files (>50k ticks) to optimize performance.

        Args:
            parquet_file: Path to parquet file

        Returns:
            Index entry dict with metadata and statistics
        """
        pq_file = pq.ParquetFile(parquet_file)

        try:
            symbol = parquet_file.name.split('_')[0]
        except IndexError:
            symbol = "UNKNOWN"

        # === BASIC METADATA (unchanged) ===
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
        # Load DataFrame for statistics (with sampling for large files)
        if tick_count > 50000:
            # Sample 10% for spread statistics (performance optimization)
            df = pd.read_parquet(parquet_file)
            sample_size = max(5000, int(tick_count * 0.1))
            df_sample = df.sample(n=min(sample_size, len(df)))

            # Calculate spread from sample
            avg_spread_points = float(
                df_sample['spread_points'].mean()) if 'spread_points' in df_sample else None
            avg_spread_pct = float(
                df_sample['spread_pct'].mean()) if 'spread_pct' in df_sample else None

            # Sessions need full scan (no sampling)
            sessions = df['session'].value_counts(
            ).to_dict() if 'session' in df else {}
        else:
            # Small file: full scan
            df = pd.read_parquet(parquet_file)

            avg_spread_points = float(
                df['spread_points'].mean()) if 'spread_points' in df else None
            avg_spread_pct = float(df['spread_pct'].mean()
                                   ) if 'spread_pct' in df else None
            sessions = df['session'].value_counts(
            ).to_dict() if 'session' in df else {}

        # Calculate tick frequency (ticks per second)
        duration_seconds = (end_time - start_time).total_seconds()
        tick_frequency = round(tick_count / duration_seconds,
                               2) if duration_seconds > 0 else 0.0

        broker_type_raw = custom_metadata.get(b'broker_type')
        if broker_type_raw:
            broker_type = broker_type_raw.decode('utf-8')
        else:
            # TEMPORARY FALLBACK: Support old files with data_collector
            broker_type = custom_metadata.get(
                b'data_collector', b'mt5').decode('utf-8')

        market_type = custom_metadata.get(
            b'market_type', b'forex_cfd').decode('utf-8')

        # === BUILD EXTENDED INDEX ENTRY ===
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
        """
        Check if index needs rebuilding.
        """
        if not self.index_file.exists():
            return True

        index_mtime = self.index_file.stat().st_mtime

        # CHANGED: Nur Tick-Files prüfen
        parquet_files = list(self.data_dir.glob("*/ticks/**/*.parquet"))
        if parquet_files:
            newest_parquet = max(f.stat().st_mtime for f in parquet_files)

            if newest_parquet > index_mtime:
                self.logger.info(
                    "📋 Index outdated - newer Parquet files found")
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

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbol: Trading symbol
            start_date: Start datetime (UTC)
            end_date: End datetime (UTC)

        Returns:
            List of relevant file paths
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
    # INDEX PERSISTENCE
    # =========================================================================

    def save_index(self) -> None:
        """Save index to JSON file """
        index_data = {
            'created_at': datetime.now(timezone.utc).isoformat(),
            'data_dir': str(self.data_dir),
            'symbols': self.index
        }

        with open(self.index_file, 'w') as f:
            json.dump(index_data, f, indent=2)

        self.logger.debug(f"💾 Index saved to {self.index_file}")

    def load_index(self) -> None:
        """Load index from JSON file """
        try:
            with open(self.index_file, 'r') as f:
                data = json.load(f)
                self.index = data['symbols']
        except Exception as e:
            self.logger.warning(f"Failed to load index: {e}")
            self.index = {}

    # =========================================================================
    # COVERAGE REPORTS
    # =========================================================================

    def get_coverage_report(self, broker_type: BrokerType, symbol: str) -> CoverageReport:
        """
        Generate coverage report for a symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol

        Returns:
            CoverageReport instance or None
        """
        if broker_type not in self.index:
            self.logger.warning(
                f"Broker type '{broker_type}' not found in index")
            return None

        if symbol not in self.index[broker_type]:
            self.logger.warning(
                f"Symbol '{symbol}' not found in index for broker_type '{broker_type}'")
            return None

        report = CoverageReport(
            symbol, broker_type=broker_type)
        report.analyze()
        return report

    def get_symbol_coverage(self, broker_type: str, symbol: str) -> Dict:
        """
        Get basic coverage statistics for a symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol

        Returns:
            Dict with coverage statistics
        """
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
        """
        List all available symbols.

        Args:
            broker_type: If provided, list symbols for this broker_type only.
                        If None, list all symbols across all broker_types.

        Returns:
            Sorted list of symbol names
        """
        if broker_type:
            if broker_type not in self.index:
                return []
            return sorted(self.index[broker_type].keys())

        # All symbols across all broker_types
        all_symbols = set()
        for bt in self.index:
            all_symbols.update(self.index[bt].keys())
        return sorted(all_symbols)

    def list_broker_types(self) -> List[str]:
        """
        List all available broker types.

        Returns:
            Sorted list of broker_type names
        """
        return sorted(self.index.keys())

    def print_summary(self) -> None:
        """Print index summary grouped by broker_type"""
        print("\n" + "="*60)
        print("📚 Parquet Index Summary")
        print("="*60)

        if not self.index:
            print("   (empty index)")
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
        """
        Print coverage report for a symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
        """
        report = self.get_coverage_report(broker_type, symbol)
        if report is not None:
            print(report.generate_report())
