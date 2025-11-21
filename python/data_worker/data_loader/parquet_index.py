"""
ParquetIndexManager - Fast File Selection via Metadata Index

- ALT: ticks/mt5/EURUSD/*.parquet
- NEU: mt5/ticks/EURUSD/*.parquet
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow.parquet as pq

from python.components.logger.abstract_logger import AbstractLogger
from python.framework.reporting.coverage_report import (
    TimeRangeCoverageReport,
    IndexEntry
)

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class ParquetIndexManager:
    """
    Manages Parquet file index for fast time-based file selection.

    """

    def __init__(self, data_dir: Path, logger: AbstractLogger = vLog):
        self.logger = logger
        self.data_dir = Path(data_dir)
        # CHANGED: Index-Dateiname für Ticks
        self.index_file = self.data_dir / ".parquet_tick_index.json"
        self.index: Dict[str, List[Dict]] = {}

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
                symbol = entry['symbol']

                if symbol not in self.index:
                    self.index[symbol] = []

                self.index[symbol].append(entry)

            except Exception as e:
                self.logger.warning(
                    f"Failed to index {parquet_file.name}: {e}")

        # Sort files chronologically per symbol
        for symbol in self.index:
            self.index[symbol].sort(key=lambda x: x['start_time'])

        self.save_index()

        elapsed = time.time() - start_time
        total_files = sum(len(files) for files in self.index.values())
        self.logger.info(
            f"✅ Index built: {total_files} files across {len(self.index)} symbols "
            f"in {elapsed:.2f}s"
        )

    def _scan_file(self, parquet_file: Path) -> Dict:
        """
        Scan single Parquet file and extract metadata.
        [UNCHANGED - works with any path structure]
        """
        pq_file = pq.ParquetFile(parquet_file)

        try:
            symbol = parquet_file.name.split('_')[0]
        except IndexError:
            symbol = "UNKNOWN"

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

        return {
            'file': parquet_file.name,
            'path': str(parquet_file.absolute()),
            'symbol': symbol,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'tick_count': pq_file.metadata.num_rows,
            'file_size_mb': round(parquet_file.stat().st_size / (1024 * 1024), 2),
            'source_file': source_file,
            'num_row_groups': pq_file.num_row_groups
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
    # FILE SELECTION - UNCHANGED
    # =========================================================================

    def get_relevant_files(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Path]:
        """Find ONLY files covering requested time range """
        if symbol not in self.index:
            self.logger.warning(f"Symbol '{symbol}' not found in index")
            return []

        relevant = []

        for entry in self.index[symbol]:
            file_start = pd.to_datetime(entry['start_time'], utc=True)
            file_end = pd.to_datetime(entry['end_time'], utc=True)

            if file_start <= end_date and file_end >= start_date:
                relevant.append(Path(entry['path']))

        return relevant

    # =========================================================================
    # INDEX PERSISTENCE - UNCHANGED
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
    # COVERAGE REPORTS - UNCHANGED
    # =========================================================================

    def get_coverage_report(self, symbol: str) -> TimeRangeCoverageReport:
        """Generate coverage report for a symbol """
        if symbol not in self.index:
            self.logger.warning(f"Symbol '{symbol}' not found in index")
            return TimeRangeCoverageReport(symbol, [], [], [])

        entries = self.index[symbol]

        index_entries = [
            IndexEntry(
                file=entry['file'],
                path=entry['path'],
                symbol=entry['symbol'],
                start_time=pd.to_datetime(entry['start_time']),
                end_time=pd.to_datetime(entry['end_time']),
                tick_count=entry['tick_count'],
                file_size_mb=entry['file_size_mb'],
                source_file=entry['source_file'],
                num_row_groups=entry['num_row_groups']
            )
            for entry in entries
        ]

        report = TimeRangeCoverageReport(symbol, index_entries)
        report.analyze()
        return report

    def get_symbol_coverage(self, symbol: str) -> Dict:
        """Get basic coverage statistics for a symbol """
        if symbol not in self.index:
            return {}

        entries = self.index[symbol]

        return {
            'num_files': len(entries),
            'total_ticks': sum(e['tick_count'] for e in entries),
            'total_size_mb': sum(e['file_size_mb'] for e in entries),
            'start_time': entries[0]['start_time'],
            'end_time': entries[-1]['end_time'],
            'files': [e['file'] for e in entries]
        }

    # =========================================================================
    # UTILITY METHODS - UNCHANGED
    # =========================================================================

    def list_symbols(self) -> List[str]:
        """List all available symbols """
        return sorted(self.index.keys())

    def print_summary(self) -> None:
        """Print index summary """
        print("\n" + "="*60)
        print("📚 Parquet Index Summary")
        print("="*60)

        if not self.index:
            print("   (empty index)")
            return

        for symbol in sorted(self.index.keys()):
            coverage = self.get_symbol_coverage(symbol)
            print(f"\n{symbol}:")
            print(f"   Files:      {coverage['num_files']}")
            print(f"   Ticks:      {coverage['total_ticks']:,}")
            print(f"   Size:       {coverage['total_size_mb']:.1f} MB")
            print(
                f"   Range:      {coverage['start_time'][:10]} → {coverage['end_time'][:10]}")

        print("="*60 + "\n")

    def print_coverage_report(self, symbol: str) -> None:
        """Print coverage report for a symbol """
        report = self.get_coverage_report(symbol)
        print(report.generate_report())
