"""
Parquet Bars Index Manager - Fast Bar File Selection
====================================================

Manages index for pre-rendered bar files.
Enables O(1) file selection for warmup and backtesting.

Analog to ParquetIndexManager but for bars instead of ticks.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow.parquet as pq

from python.components.logger.abstract_logger import AbstractLogger
from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class ParquetBarsIndexManager:
    """
    Manages index for pre-rendered bar parquet files.

    Structure mirrors tick index but for bars:
    - Scans mt5/bars/**/*.parquet
    - Builds .parquet_bars_index.json
    - Enables fast bar file selection
    """

    def __init__(self, data_dir: Path,  logger: AbstractLogger = vLog):
        """
        Initialize bar index manager.

        Args:
            data_dir: Root data directory (e.g., ./data/processed/)
        """
        self.data_dir = Path(data_dir)
        self.index_file = self.data_dir / ".parquet_bars_index.json"
        # {symbol: {timeframe: entry}}
        self.index: Dict[str, Dict[str, Dict]] = {}
        self.logger = logger

    # =========================================================================
    # INDEX BUILDING
    # =========================================================================

    def build_index(self,
                    force_rebuild: bool = False) -> None:
        """
        Build or load index from bar parquet files.

        Scans: */bars/**/*.parquet

        Args:
            force_rebuild: Always rebuild, ignore existing index
        """
        # Check if rebuild needed
        if not force_rebuild and not self.needs_rebuild():
            self.load_index()
            self.logger.info(
                f"📚 Loaded existing bar index ({len(self.index)} symbols)")
            return

        self.logger.info("🔍 Scanning bar files for index...")
        start_time = time.time()

        # Scan pattern: */bars/**/*.parquet
        # Example: mt5/bars/EURUSD/EURUSD_M5_BARS.parquet
        bar_files = list(self.data_dir.glob("*/bars/**/*_BARS.parquet"))

        if not bar_files:
            self.logger.warning(f"No bar files found in {self.data_dir}")
            self.index = {}
            return

        # Process each file
        for bar_file in bar_files:
            try:
                entry = self._scan_bar_file(bar_file)
                symbol = entry['symbol']
                timeframe = entry['timeframe']

                if symbol not in self.index:
                    self.index[symbol] = {}

                self.index[symbol][timeframe] = entry

            except Exception as e:
                self.logger.warning(f"Failed to index {bar_file.name}: {e}")

        # Save index
        self.save_index()

        elapsed = time.time() - start_time
        total_entries = sum(len(tfs) for tfs in self.index.values())
        self.logger.info(
            f"✅ Bar index built: {total_entries} timeframes across "
            f"{len(self.index)} symbols in {elapsed:.2f}s"
        )

    def _scan_bar_file(self, bar_file: Path) -> Dict:
        """
        Scan single bar parquet file and extract metadata.

        Extended to include tick statistics and bar type distribution
        for market analysis and scenario generation.

        Args:
            bar_file: Path to bar parquet file

        Returns:
            Index entry dict with metadata and aggregated statistics
        """
        # Open parquet file (metadata-only first)
        pq_file = pq.ParquetFile(bar_file)

        # Extract metadata
        custom_metadata = pq_file.metadata.metadata

        # Parse metadata (bytes → str)
        metadata = {
            key.decode('utf-8') if isinstance(key, bytes) else key:
            value.decode('utf-8') if isinstance(value, bytes) else value
            for key, value in custom_metadata.items()
        }

        # Extract key info
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

        # === NEW: Load full DataFrame for aggregations ===
        # Required for tick_count and bar_type statistics
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
        hybrid_bar_count = int((df['bar_type'] == 'hybrid').sum())

        # Build index entry
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

            # NEW: Tick statistics for scenario generation
            'total_tick_count': total_tick_count,
            'avg_ticks_per_bar': round(avg_ticks_per_bar, 2),
            'min_ticks_per_bar': min_ticks_per_bar,
            'max_ticks_per_bar': max_ticks_per_bar,

            # NEW: Bar type distribution for quality analysis
            'real_bar_count': real_bar_count,
            'synthetic_bar_count': synthetic_bar_count,
            'hybrid_bar_count': hybrid_bar_count,
        }

    def needs_rebuild(self) -> bool:
        """
        Check if index needs rebuilding.

        Triggers:
        - Index file doesn't exist
        - Index is older than newest bar file

        Returns:
            True if rebuild needed
        """
        if not self.index_file.exists():
            return True

        index_mtime = self.index_file.stat().st_mtime

        # Check for newer bar files
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
        symbol: str,
        timeframe: str
    ) -> Optional[Path]:
        """
        Get bar file path for symbol/timeframe.

        THIS IS THE CORE FUNCTION!
        Returns the single bar file for warmup or backtesting.

        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            timeframe: Timeframe (e.g., 'M5')

        Returns:
            Path to bar file or None if not found

        Example:
            >>> index = ParquetBarsIndexManager(data_dir)
            >>> bar_file = index.get_bar_file('EURUSD', 'M5')
            >>> bars = pd.read_parquet(bar_file)  # Load M5 bars instantly!
        """
        if symbol not in self.index:
            self.logger.warning(f"Symbol '{symbol}' not found in bar index")
            return None

        if timeframe not in self.index[symbol]:
            self.logger.warning(
                f"Timeframe '{timeframe}' not found for {symbol} in bar index"
            )
            return None

        entry = self.index[symbol][timeframe]
        return Path(entry['path'])

    def get_available_timeframes(self, symbol: str) -> List[str]:
        """
        Get list of available timeframes for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            List of available timeframes (e.g., ['M1', 'M5', 'M15'])
        """
        if symbol not in self.index:
            return []

        return sorted(self.index[symbol].keys())

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

        self.logger.debug(f"💾 Bar index saved to {self.index_file}")

    def load_index(self) -> None:
        """Load index from JSON file"""
        try:
            with open(self.index_file, 'r') as f:
                data = json.load(f)
                self.index = data['symbols']
        except Exception as e:
            self.logger.warning(f"Failed to load bar index: {e}")
            self.index = {}

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def list_symbols(self) -> List[str]:
        """
        List all available symbols in bar index.

        Returns:
            Sorted list of symbol names
        """
        return sorted(self.index.keys())

    def get_symbol_stats(self, symbol: str) -> Dict:
        """
        Get statistics for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Dict with statistics per timeframe
        """
        if symbol not in self.index:
            return {}

        stats = {}
        for timeframe, entry in self.index[symbol].items():
            stats[timeframe] = {
                'bar_count': entry['bar_count'],
                'file_size_mb': entry['file_size_mb'],
                'start_time': entry['start_time'],
                'end_time': entry['end_time']
            }

        return stats

    def print_summary(self) -> None:
        """Print bar index summary"""
        print("\n" + "="*60)
        print("📊 Bar Index Summary")
        print("="*60)

        if not self.index:
            print("   (empty index)")
            return

        for symbol in sorted(self.index.keys()):
            print(f"\n{symbol}:")
            stats = self.get_symbol_stats(symbol)

            for timeframe in sorted(stats.keys()):
                tf_stats = stats[timeframe]
                print(f"   {timeframe}: {tf_stats['bar_count']:,} bars "
                      f"({tf_stats['file_size_mb']:.1f} MB)")

        print("="*60 + "\n")
