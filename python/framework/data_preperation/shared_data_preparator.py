"""
FiniexTestingIDE - Shared Data Preparator (UTC-FIXED)
Loads and prepares data for all scenarios (SERIAL, Main Process)

PHASE 1: Data Preparation (Serial, Main Process)

UTC-FIX:
- All timestamp comparisons are UTC-aware
- Converts parquet timestamps to UTC-aware before filtering
- Prevents timezone comparison errors
"""

from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
import pandas as pd

from python.framework.types.process_data_types import (
    RequirementsMap,
    ProcessDataPackage,
    TickRequirement,
    BarRequirement
)
from python.data_worker.data_loader.parquet_index import ParquetIndexManager
from python.data_worker.data_loader.parquet_bars_index import ParquetBarsIndexManager

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class SharedDataPreparator:
    """
    Prepares shared data for all scenarios.

    WORKFLOW:
    1. Load tick/bar indices (via managers)
    2. Process RequirementsMap
    3. Load data from parquet files
    4. Filter and package data
    5. Return ProcessDataPackage

    COW OPTIMIZATION:
    - All data stored as tuples (immutable)
    - Subprocesses share memory (0 copy!)
    - Only writes trigger memory copy

    UTC-FIX:
    - All timestamp comparisons are UTC-aware
    - Prevents pandas datetime comparison errors
    """

    def __init__(self, data_dir: str = "data/processed"):
        """
        Initialize data preparator with index managers.

        Args:
            data_dir: Root data directory containing parquet files and indices
        """
        self.data_dir = Path(data_dir)

        # Use existing index managers
        vLog.info("📚 Initializing index managers...")

        # Tick index manager
        self.tick_index_manager = ParquetIndexManager(self.data_dir)
        self.tick_index_manager.build_index()  # Auto-loads or rebuilds

        # Bar index manager
        self.bar_index_manager = ParquetBarsIndexManager(self.data_dir)
        self.bar_index_manager.build_index()  # Auto-loads or rebuilds

        vLog.info(
            f"✅ Indices loaded: "
            f"{len(self.tick_index_manager.list_symbols())} tick symbols, "
            f"{len(self.bar_index_manager.list_symbols())} bar symbols"
        )

    def prepare_all(self, requirements: RequirementsMap) -> ProcessDataPackage:
        """
        Prepare all data based on requirements.

        MAIN ENTRY POINT for data preparation.

        Args:
            requirements: Aggregated requirements from all scenarios

        Returns:
            ProcessDataPackage with all prepared data
        """
        vLog.info("🔄 Starting data preparation...")

        # === PHASE 1A: Load Ticks ===
        ticks_data, tick_counts, tick_ranges = self._prepare_ticks(
            requirements.tick_requirements
        )

        # === PHASE 1B: Load Bars ===
        bars_data, bar_counts = self._prepare_bars(
            requirements.bar_requirements
        )

        # === PHASE 1C: Package Data ===
        package = ProcessDataPackage(
            ticks=ticks_data,
            bars=bars_data,
            tick_counts=tick_counts,
            tick_ranges=tick_ranges,
            bar_counts=bar_counts
        )

        # Log summary
        total_ticks = sum(tick_counts.values())
        total_bars = sum(bar_counts.values())
        vLog.info(
            f"✅ Data prepared: {total_ticks:,} ticks, {total_bars:,} bars "
            f"({len(ticks_data)} tick sets, {len(bars_data)} bar sets)"
        )

        return package

    def _prepare_ticks(
        self,
        requirements: List[TickRequirement]
    ) -> Tuple[Dict[str, Tuple[Any, ...]], Dict[str, int], Dict[str, Tuple[datetime, datetime]]]:
        """
        Prepare tick data for all requirements.

        Args:
            requirements: List of tick requirements

        Returns:
            (ticks_data, tick_counts, tick_ranges)
        """
        ticks_data = {}
        tick_counts = {}
        tick_ranges = {}

        for req in requirements:
            vLog.info(f"📊 Loading ticks for {req.symbol}...")

            # Determine loading strategy
            if req.max_ticks is not None:
                # Tick-limited mode
                ticks, count, time_range = self._load_ticks_tick_mode(
                    symbol=req.symbol,
                    start_time=req.start_time,
                    max_ticks=req.max_ticks
                )
            else:
                # Timespan mode
                ticks, count, time_range = self._load_ticks_timespan_mode(
                    symbol=req.symbol,
                    start_time=req.start_time,
                    end_time=req.end_time
                )

            # Store as tuple (immutable, CoW-friendly)
            ticks_data[req.scenario_name] = tuple(ticks)
            tick_counts[req.scenario_name] = count
            tick_ranges[req.scenario_name] = time_range

            vLog.info(
                f"  ✅ {count:,} ticks loaded ({time_range[0]} → {time_range[1]})")

        return ticks_data, tick_counts, tick_ranges

    def _load_ticks_tick_mode(
        self,
        symbol: str,
        start_time: datetime,
        max_ticks: int
    ) -> Tuple[List[Any], int, Tuple[datetime, datetime]]:
        """
        Load ticks in tick-limited mode.

        UTC-FIX: Ensures all timestamp comparisons are UTC-aware.

        Args:
            symbol: Symbol to load
            start_time: Start time (UTC-aware)
            max_ticks: Maximum ticks to load

        Returns:
            (ticks, count, time_range)
        """
        # Get files from index
        if symbol not in self.tick_index_manager.index:
            raise ValueError(f"Symbol {symbol} not found in tick index")

        files = self.tick_index_manager.index[symbol]

        # UTC-FIX: Ensure start_time is UTC-aware
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        # Find starting file
        start_file_idx = None
        for idx, file_info in enumerate(files):
            file_start = pd.to_datetime(file_info['start_time'], utc=True)
            file_end = pd.to_datetime(file_info['end_time'], utc=True)

            if file_start <= start_time <= file_end:
                start_file_idx = idx
                break

        if start_file_idx is None:
            # Start time not in any file, find next file after start_time
            for idx, file_info in enumerate(files):
                file_start = pd.to_datetime(file_info['start_time'], utc=True)
                if file_start >= start_time:
                    start_file_idx = idx
                    break

        if start_file_idx is None:
            raise ValueError(
                f"No tick data found for {symbol} starting at {start_time}")

        # Load files until max_ticks reached
        all_ticks = []
        for file_info in files[start_file_idx:]:
            df = pd.read_parquet(file_info['path'])

            # UTC-FIX: Convert timestamps to UTC-aware
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

            # Filter to start_time
            if len(all_ticks) == 0:
                df = df[df['timestamp'] >= start_time]

            # Convert to dicts
            all_ticks.extend(df.to_dict('records'))

            # Check if we have enough
            if len(all_ticks) >= max_ticks:
                break

        # Slice to exact count
        all_ticks = all_ticks[:max_ticks]

        if not all_ticks:
            raise ValueError(
                f"No ticks loaded for {symbol} (check start_time)")

        # Get time range
        time_range = (
            all_ticks[0]['timestamp'],
            all_ticks[-1]['timestamp']
        )

        return all_ticks, len(all_ticks), time_range

    def _load_ticks_timespan_mode(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime
    ) -> Tuple[List[Any], int, Tuple[datetime, datetime]]:
        """
        Load ticks in timespan mode.

        UTC-FIX: Ensures all timestamp comparisons are UTC-aware.

        Args:
            symbol: Symbol to load
            start_time: Start time (UTC-aware)
            end_time: End time (UTC-aware)

        Returns:
            (ticks, count, time_range)
        """
        # UTC-FIX: Ensure times are UTC-aware
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        # Use manager's API to get relevant files
        relevant_files = self.tick_index_manager.get_relevant_files(
            symbol=symbol,
            start_date=start_time,
            end_date=end_time
        )

        if not relevant_files:
            raise ValueError(
                f"No tick data found for {symbol} in range {start_time} - {end_time}"
            )

        # Load and concatenate files
        all_ticks = []
        for file_path in relevant_files:
            df = pd.read_parquet(file_path)

            # UTC-FIX: Convert timestamps to UTC-aware
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

            # Filter to time range
            df = df[(df['timestamp'] >= start_time)
                    & (df['timestamp'] <= end_time)]

            all_ticks.extend(df.to_dict('records'))

        if not all_ticks:
            raise ValueError(
                f"No ticks found for {symbol} in range {start_time} - {end_time} "
                f"(files loaded but empty after filtering)"
            )

        # Get time range
        time_range = (
            all_ticks[0]['timestamp'],
            all_ticks[-1]['timestamp']
        )

        return all_ticks, len(all_ticks), time_range

    def _prepare_bars(
        self,
        requirements: List[BarRequirement]
    ) -> Tuple[Dict[Tuple[str, str, datetime], Tuple[Any, ...]], Dict[Tuple[str, str, datetime], int]]:
        """
        Prepare bar data for all requirements.

        UTC-FIX: Ensures timestamp comparisons are UTC-aware.

        Args:
            requirements: List of bar requirements

        Returns:
            (bars_data, bar_counts)
        """
        bars_data = {}
        bar_counts = {}

        # Group by (symbol, timeframe) to load each file once
        by_symbol_tf: Dict[Tuple[str, str], List[BarRequirement]] = {}
        for req in requirements:
            key = (req.symbol, req.timeframe)
            if key not in by_symbol_tf:
                by_symbol_tf[key] = []
            by_symbol_tf[key].append(req)

        # Load and filter for each (symbol, timeframe)
        for (symbol, timeframe), reqs in by_symbol_tf.items():
            vLog.info(f"📊 Loading bars for {symbol} {timeframe}...")

            # Use manager's API to get bar file
            bar_file = self.bar_index_manager.get_bar_file(symbol, timeframe)

            if bar_file is None:
                vLog.error(
                    f"Bar file not found for {symbol} {timeframe} - "
                    f"available timeframes: {self.bar_index_manager.get_available_timeframes(symbol)}"
                )
                continue

            # Load bar file
            bars_df = pd.read_parquet(bar_file)

            # Ensure timestamp column exists and is UTC-aware
            if 'timestamp' not in bars_df.columns and 'time' in bars_df.columns:
                bars_df['timestamp'] = bars_df['time']

            # UTC-FIX: Convert timestamps to UTC-aware
            if 'timestamp' in bars_df.columns:
                bars_df['timestamp'] = pd.to_datetime(
                    bars_df['timestamp'], utc=True)

            # Filter for each unique start_time
            for req in reqs:
                # UTC-FIX: Ensure req.start_time is UTC-aware
                req_start_time = req.start_time
                if req_start_time.tzinfo is None:
                    req_start_time = req_start_time.replace(
                        tzinfo=timezone.utc)

                # Get warmup bars (bars BEFORE start_time)
                warmup_bars_df = bars_df[bars_df['timestamp'] < req_start_time]

                # Take last N bars (warmup_count)
                warmup_bars_df = warmup_bars_df.tail(req.warmup_count)

                # Validate count
                if len(warmup_bars_df) < req.warmup_count:
                    vLog.warning(
                        f"  ⚠️  Only {len(warmup_bars_df)} warmup bars available "
                        f"(requested {req.warmup_count}) for {symbol} {timeframe} "
                        f"before {req_start_time}"
                    )

                # Convert to records and tuple
                # Force UTC timezone before serialization
                warmup_bars_df['timestamp'] = warmup_bars_df['timestamp'].dt.tz_convert(
                    'UTC')
                bars_list = warmup_bars_df.to_dict('records')

                key = (symbol, timeframe, req.start_time)
                bars_data[key] = tuple(bars_list)
                bar_counts[key] = len(bars_list)

                vLog.info(
                    f"  ✅ {len(bars_list)} warmup bars filtered "
                    f"(before {req_start_time})"
                )

        return bars_data, bar_counts
