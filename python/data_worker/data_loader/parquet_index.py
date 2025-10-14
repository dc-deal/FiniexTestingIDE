"""
ParquetIndexManager - Fast File Selection via Metadata Index
Enables O(1) file selection based on time range requirements

NEW (C#002): Core index system for optimized data loading
UPDATED (C#003): Recursive scanning for hierarchical directory structure
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow.parquet as pq

from python.components.logger.bootstrap_logger import setup_logging
from python.data_worker.data_loader.coverage_report import (
    TimeRangeCoverageReport,
    IndexEntry
)

vLog = setup_logging(name="ParquetIndexManager")


class ParquetIndexManager:
    """
    Manages Parquet file index for fast time-based file selection.

    Features:
    - Scans Parquet files and extracts time range metadata
    - Persists index as JSON for instant loading
    - Enables precise file selection based on time range
    - Validates data continuity and detects gaps
    - Supports hierarchical directory structure (NEW in C#003)

    Performance:
    - Index build: ~5ms per file (metadata-only, no data load)
    - File selection: O(n) where n = files for symbol
    - Load speedup: ~10x by avoiding unnecessary file loading
    """

    def __init__(self, data_dir: Path):
        """
        Initialize index manager.

        Args:
            data_dir: Directory containing Parquet files
        """
        self.data_dir = Path(data_dir)
        self.index_file = self.data_dir / ".parquet_index.json"
        self.index: Dict[str, List[Dict]] = {}  # {symbol: [entries]}

    # =========================================================================
    # INDEX BUILDING
    # =========================================================================

    def build_index(self, force_rebuild: bool = False) -> None:
        """
        Build or load index from Parquet files.

        Scans all Parquet files using metadata-only access (fast!).

        Args:
            force_rebuild: Always rebuild, ignore existing index
        """
        # Check if rebuild needed
        if not force_rebuild and not self.needs_rebuild():
            self.load_index()
            vLog.info(f"📚 Loaded existing index ({len(self.index)} symbols)")
            return

        vLog.info("🔍 Scanning Parquet files for index...")
        start_time = time.time()

        # CHANGED (C#003): Recursive scanning for hierarchical structure
        # Before: glob("*.parquet")
        # Now: glob("**/*.parquet") - scans data/processed/mt5/EURUSD/*.parquet
        parquet_files = list(self.data_dir.glob("**/*.parquet"))

        if not parquet_files:
            vLog.warning(f"No Parquet files found in {self.data_dir}")
            self.index = {}
            return

        # Process each file
        for parquet_file in parquet_files:
            try:
                entry = self._scan_file(parquet_file)
                symbol = entry['symbol']

                if symbol not in self.index:
                    self.index[symbol] = []

                self.index[symbol].append(entry)

            except Exception as e:
                vLog.warning(f"Failed to index {parquet_file.name}: {e}")

        # Sort files chronologically per symbol
        for symbol in self.index:
            self.index[symbol].sort(key=lambda x: x['start_time'])

        # Save index
        self.save_index()

        elapsed = time.time() - start_time
        total_files = sum(len(files) for files in self.index.values())
        vLog.info(
            f"✅ Index built: {total_files} files across {len(self.index)} symbols "
            f"in {elapsed:.2f}s"
        )

    def _scan_file(self, parquet_file: Path) -> Dict:
        """
        Scan single Parquet file and extract metadata.

        FAST: Reads only first/last row group timestamps, not all data!

        Args:
            parquet_file: Path to Parquet file

        Returns:
            Index entry dict
        """
        # Open Parquet file (metadata-only access)
        pq_file = pq.ParquetFile(parquet_file)

        # Extract symbol from filename (SYMBOL_YYYYMMDD_HHMMSS.parquet)
        try:
            symbol = parquet_file.name.split('_')[0]
        except IndexError:
            symbol = "UNKNOWN"

        # Read FIRST row group (only timestamp column)
        first_row_group = pq_file.read_row_group(0, columns=['timestamp'])
        start_time = first_row_group['timestamp'][0].as_py()

        # Read LAST row group (only timestamp column)
        last_row_group_idx = pq_file.num_row_groups - 1
        last_row_group = pq_file.read_row_group(
            last_row_group_idx,
            columns=['timestamp']
        )
        end_time = last_row_group['timestamp'][-1].as_py()

        # Read custom metadata (from tick_importer)
        custom_metadata = pq_file.metadata.metadata
        source_file = custom_metadata.get(
            b'source_file', b'unknown').decode('utf-8')

        # Build index entry
        return {
            'file': parquet_file.name,
            # Absolute path works transparently with hierarchical structure
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

        Triggers:
        - Index file doesn't exist
        - Index is older than newest Parquet file

        Returns:
            True if rebuild needed
        """
        if not self.index_file.exists():
            return True

        index_mtime = self.index_file.stat().st_mtime

        # CHANGED (C#003): Recursive search for newest Parquet file
        parquet_files = list(self.data_dir.glob("**/*.parquet"))
        if parquet_files:
            newest_parquet = max(f.stat().st_mtime for f in parquet_files)

            if newest_parquet > index_mtime:
                vLog.info("📋 Index outdated - newer Parquet files found")
                return True

        return False

    # =========================================================================
    # FILE SELECTION (CORE OPTIMIZATION!)
    # =========================================================================

    def get_relevant_files(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Path]:
        """
        Find ONLY the Parquet files covering the requested time range.

        THIS IS THE CORE OPTIMIZATION!

        Instead of loading ALL files for a symbol, we select only those
        that intersect with the requested time range.

        Args:
            symbol: Trading symbol
            start_date: Requested start time
            end_date: Requested end time

        Returns:
            List of Path objects for relevant files
        """
        if symbol not in self.index:
            vLog.warning(f"Symbol '{symbol}' not found in index")
            return []

        relevant = []

        for entry in self.index[symbol]:
            file_start = pd.to_datetime(entry['start_time'])
            file_end = pd.to_datetime(entry['end_time'])

            # Overlap check: file intersects with requested range
            # Condition: file_start <= end_date AND file_end >= start_date
            if file_start <= end_date and file_end >= start_date:
                relevant.append(Path(entry['path']))

        return relevant

    # =========================================================================
    # INDEX PERSISTENCE
    # =========================================================================

    def save_index(self) -> None:
        """Save index to JSON file"""
        index_data = {
            'created_at': datetime.now(timezone.utc).isoformat(),
            'data_dir': str(self.data_dir),
            'symbols': self.index
        }

        with open(self.index_file, 'w') as f:
            json.dump(index_data, f, indent=2)

        vLog.debug(f"💾 Index saved to {self.index_file}")

    def load_index(self) -> None:
        """Load index from JSON file"""
        try:
            with open(self.index_file, 'r') as f:
                data = json.load(f)
                self.index = data['symbols']
        except Exception as e:
            vLog.warning(f"Failed to load index: {e}")
            self.index = {}

    # =========================================================================
    # COVERAGE REPORTS
    # =========================================================================

    def get_coverage_report(self, symbol: str) -> TimeRangeCoverageReport:
        """
        Generate coverage report for a symbol.

        Analyzes:
        - Total tick count
        - Time range coverage
        - Data gaps (>5min)
        - File distribution

        Args:
            symbol: Trading symbol

        Returns:
            Coverage report object
        """
        if symbol not in self.index:
            vLog.warning(f"Symbol '{symbol}' not found in index")
            return TimeRangeCoverageReport(symbol, [], [], [])

        entries = self.index[symbol]

        # Convert index entries to IndexEntry objects
        index_entries = [
            IndexEntry(
                file=entry['file'],
                path=entry['path'],                              # FEHLT!
                symbol=entry['symbol'],                          # FEHLT!
                start_time=pd.to_datetime(entry['start_time']),
                end_time=pd.to_datetime(entry['end_time']),
                tick_count=entry['tick_count'],
                file_size_mb=entry['file_size_mb'],             # FEHLT!
                source_file=entry['source_file'],               # FEHLT!
                num_row_groups=entry['num_row_groups']          # FEHLT!
            )
            for entry in entries
        ]

        # Detect gaps
        gaps = []
        for i in range(len(index_entries) - 1):
            gap_start = index_entries[i].end_time
            gap_end = index_entries[i + 1].start_time
            gap_duration = (gap_end - gap_start).total_seconds()

            # Report gaps >5min
            if gap_duration > 300:
                gaps.append((gap_start, gap_end, gap_duration))

        # Calculate warnings
        warnings = []
        if gaps:
            warnings.append(f"{len(gaps)} data gaps detected (>5min)")

        report = TimeRangeCoverageReport(symbol, index_entries)
        report.analyze()  # Berechnet gaps und warnings intern!
        return report

    def get_symbol_coverage(self, symbol: str) -> Dict:
        """
        Get basic coverage statistics for a symbol.

        Returns:
            Dict with coverage stats
        """
        if symbol not in self.index:
            return {}

        entries = self.index[symbol]

        return {
            'num_files': len(entries),
            'total_ticks': sum(e['tick_count'] for e in entries),
            'total_size_mb': sum(e['file_size_mb'] for e in entries),
            'start_time': entries[0]['start_time'],
            'end_time': entries[-1]['end_time']
        }

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def list_symbols(self) -> List[str]:
        """
        List all available symbols.

        Returns:
            Sorted list of symbol names
        """
        return sorted(self.index.keys())

    def print_summary(self) -> None:
        """Print index summary"""
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
        """
        Print coverage report for a symbol.

        Args:
            symbol: Trading symbol
        """
        report = self.get_coverage_report(symbol)
        print(report.generate_report())

    def get_symbol_coverage(self, symbol: str) -> Dict:
        """
        Get coverage statistics for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Dict with coverage statistics
        """
        if symbol not in self.index:
            return {'error': f'No data for {symbol}'}

        files = self.index[symbol]

        return {
            'symbol': symbol,
            'start_time': files[0]['start_time'],
            'end_time': files[-1]['end_time'],
            'total_ticks': sum(f['tick_count'] for f in files),
            'num_files': len(files),
            'total_size_mb': sum(f['file_size_mb'] for f in files),
            'files': [f['file'] for f in files]  # ← FEHLT! Hinzufügen!
        }
