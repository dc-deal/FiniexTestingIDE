"""
Parquet Bars Index Manager - Fast Bar File Selection
====================================================

Manages index for pre-rendered bar files.
Enables O(1) file selection for warmup and backtesting.

Analog to TickIndexManager but for bars instead of ticks.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow.parquet as pq

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
vLog = get_global_logger()


class BarsIndexManager:
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
        # {broker_type: {symbol: {tf: entry}}}
        self.index: Dict[str, Dict[str, Dict[str, Dict]]] = {}
        self.logger = logger

    def _version_less_than(self, version: str, compare_to: str) -> bool:
        """
        Compare version strings (e.g., '1.0.5' < '1.1.0').

        Args:
            version: Version to check
            compare_to: Version to compare against

        Returns:
            True if version < compare_to
        """
        try:
            v1 = [int(x) for x in version.split('.')]
            v2 = [int(x) for x in compare_to.split('.')]
            # Pad to same length
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
                broker_type = entry['broker_type']
                symbol = entry['symbol']
                timeframe = entry['timeframe']

                # Initialize nested structure
                if broker_type not in self.index:
                    self.index[broker_type] = {}

                if symbol not in self.index[broker_type]:
                    self.index[broker_type][symbol] = {}

                self.index[broker_type][symbol][timeframe] = entry

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

        # === Load full DataFrame for aggregations ===
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

        # === Version and market type detection ===
        source_version_min = metadata.get('source_version_min', '1.0.0')
        source_version_max = metadata.get('source_version_max', '1.0.0')

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

            # Tick statistics for scenario generation
            'total_tick_count': total_tick_count,
            'avg_ticks_per_bar': round(avg_ticks_per_bar, 2),
            'min_ticks_per_bar': min_ticks_per_bar,
            'max_ticks_per_bar': max_ticks_per_bar,

            # Bar type distribution for quality analysis
            'real_bar_count': real_bar_count,
            'synthetic_bar_count': synthetic_bar_count,

            # Version metadata
            'source_version_min': source_version_min,
            'source_version_max': source_version_max,
            # legacy compatibility: data_collector
            'broker_type': metadata.get('broker_type') or metadata.get('data_collector', 'mt5'),

            # NOTE: market_type and primary_activity_metric removed
            # Use MarketConfigManager at runtime instead

            # Volume fields (null for Forex)
            'total_trade_volume': None,
            'avg_volume_per_bar': None,
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
        broker_type: str,
        symbol: str,
        timeframe: str
    ) -> Optional[Path]:
        """
        Get bar file path for broker_type/symbol/timeframe.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbol: Trading symbol (e.g., 'EURUSD')
            timeframe: Timeframe (e.g., 'M5')

        Returns:
            Path to bar file or None if not found
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
        """
        Get list of available timeframes for a symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol

        Returns:
            List of available timeframes (e.g., ['M1', 'M5', 'M15'])
        """
        if broker_type not in self.index:
            return []

        if symbol not in self.index[broker_type]:
            return []

        return sorted(self.index[broker_type][symbol].keys())

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

    def list_symbols(self, broker_type: Optional[str] = None) -> List[str]:
        """
        List all available symbols in bar index.

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

    def get_symbol_stats(self, broker_type: str, symbol: str) -> Dict:
        """
        Get statistics for a symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol

        Returns:
            Dict with statistics per timeframe
        """
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
        """Print bar index summary grouped by broker_type"""
        print("\n" + "="*60)
        print("📊 Bar Index Summary")
        print("="*60)

        if not self.index:
            print("   (empty index)")
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
