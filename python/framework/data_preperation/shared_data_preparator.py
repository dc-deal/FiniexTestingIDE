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

from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.types.process_data_types import (
    ProcessDataPackage,
    RequirementsMap,
    TickRequirement,
    BarRequirement
)
from python.data_worker.data_loader.tick_index_manager import TickIndexManager
from python.data_worker.data_loader.bars_index_manager import BarsIndexManager
from python.framework.types.scenario_set_types import SingleScenario


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

    def __init__(self, logger: ScenarioLogger, data_dir: str = "data/processed"):
        """
        Initialize data preparator with index managers.

        Args:
            data_dir: Root data directory containing parquet files and indices
        """
        self.data_dir = Path(data_dir)
        self._logger = logger

        # Use existing index managers
        self._logger.debug("📚 Initializing index managers...")

        # Tick index manager
        self.tick_index_manager = TickIndexManager(
            self.data_dir, self._logger)
        self.tick_index_manager.build_index()  # Auto-loads or rebuilds

        # Bar index manager
        self.bar_index_manager = BarsIndexManager(
            self.data_dir, self._logger)
        self.bar_index_manager.build_index()  # Auto-loads or rebuilds

        self._logger.info(
            f"✅ Indices loaded: "
            f"{len(self.tick_index_manager.list_symbols())} tick symbols, "
            f"{len(self.bar_index_manager.list_symbols())} bar symbols"
        )

    def prepare_scenario_packages(
        self,
        requirements_map: RequirementsMap,
        scenarios: List[SingleScenario],
        broker_configs: Any
    ) -> Dict[int, ProcessDataPackage]:
        """
        Prepare scenario-specific data packages (OPTIMIZATION).

        Instead of one global package (61 MB), creates individual packages
        per scenario (3-5 MB each). Reduces ProcessPool pickle overhead by 5x.

        Workflow:
        1. Load ALL data once (memory-efficient, no duplication)
        2. Filter data per scenario (symbol + time range)
        3. Package with string interning (symbol deduplication)

        Args:
            requirements_map: Aggregated requirements from all scenarios
            scenarios: List of scenarios to prepare packages for
            broker_configs: Pre-loaded broker configurations

        Returns:
            Dict mapping scenario_index → ProcessDataPackage
            Invalid scenarios are skipped (no entry in dict)
        """
        self._logger.info(
            "📦 Phase 1: Preparing scenario-specific data packages...")

        # === STEP 1: Load ALL data once (existing methods) ===
        all_ticks_dict, all_tick_counts, all_tick_ranges = self.prepare_ticks(
            requirements_map.tick_requirements
        )
        all_bars_dict, all_bar_counts = self.prepare_bars(
            requirements_map.bar_requirements
        )

        # === STEP 2: Create scenario-specific packages ===
        scenario_packages = {}

        for idx, scenario in enumerate(scenarios):
            # Skip invalid scenarios (already validated in Phase 0.5)
            if not scenario.is_valid():
                self._logger.debug(
                    f"⏭️  Skipping package for scenario {idx}: "
                    f"{scenario.name} (validation failed)"
                )
                continue

            # Filter ticks for this scenario
            scenario_ticks = self._filter_ticks_for_scenario(
                scenario, all_ticks_dict, all_tick_counts, all_tick_ranges
            )

            # Filter bars for this scenario
            scenario_bars = self._filter_bars_for_scenario(
                scenario, all_bars_dict, all_bar_counts
            )

            # Create scenario-specific package
            scenario_packages[idx] = ProcessDataPackage(
                ticks=scenario_ticks['ticks'],
                bars=scenario_bars['bars'],
                broker_configs=broker_configs,
                tick_counts=scenario_ticks['counts'],
                tick_ranges=scenario_ticks['ranges'],
                bar_counts=scenario_bars['counts']
            )

            # Log package size
            tick_count = sum(scenario_ticks['counts'].values())
            bar_count = sum(scenario_bars['counts'].values())
            self._logger.debug(
                f"✅ Package {idx} ({scenario.name}): "
                f"{tick_count:,} ticks, {bar_count} bars"
            )

        self._logger.info(
            f"✅ Created {len(scenario_packages)} scenario-specific packages"
        )

        return scenario_packages

    def _filter_ticks_for_scenario(
        self,
        scenario: SingleScenario,
        all_ticks_dict: Dict[str, Tuple[Any, ...]],
        all_tick_counts: Dict[str, int],
        all_tick_ranges: Dict[str, Tuple[datetime, datetime]]
    ) -> Dict[str, Any]:
        """
        Filter tick data for one scenario.

        OPTIMIZATION: String interning for symbol (all ticks share same reference).

        Args:
            scenario: Scenario to filter for
            all_ticks_dict: All loaded ticks (keyed by scenario_name)
            all_tick_counts: Tick counts per scenario_name
            all_tick_ranges: Time ranges per scenario_name

        Returns:
            Dict with filtered 'ticks', 'counts', 'ranges'
            Structure matches ProcessDataPackage expectations
        """
        scenario_name = scenario.name

        # Get ticks for this scenario (already loaded by scenario_name)
        if scenario_name not in all_ticks_dict:
            raise ValueError(
                f"No tick data found for scenario '{scenario_name}' "
                f"(available: {list(all_ticks_dict.keys())})"
            )

        ticks_tuple = all_ticks_dict[scenario_name]

        # === STRING INTERNING OPTIMIZATION ===
        # All ticks share same symbol reference → reduces pickle size
        symbol_intern = scenario.symbol

        ticks_list = list(ticks_tuple)
        for tick in ticks_list:
            tick['symbol'] = symbol_intern  # Replace with interned string

        # Repackage as tuple (immutable, CoW-friendly)
        ticks_tuple = tuple(ticks_list)

        # Return as dict with single key (scenario symbol)
        # Structure: Dict[str, Tuple] matches ProcessDataPackage.ticks
        return {
            'ticks': {scenario.symbol: ticks_tuple},
            'counts': {scenario.symbol: all_tick_counts[scenario_name]},
            'ranges': {scenario.symbol: all_tick_ranges[scenario_name]}
        }

    def _filter_bars_for_scenario(
        self,
        scenario: SingleScenario,
        all_bars_dict: Dict[Tuple[str, str, datetime], Tuple[Any, ...]],
        all_bar_counts: Dict[Tuple[str, str, datetime], int]
    ) -> Dict[str, Any]:
        """
        Filter bar data for one scenario.

        OPTIMIZATION: String interning for symbol + timeframe.

        Args:
            scenario: Scenario to filter for
            all_bars_dict: All loaded bars (keyed by symbol, timeframe, start_time)
            all_bar_counts: Bar counts per key

        Returns:
            Dict with filtered 'bars', 'counts'
            Structure matches ProcessDataPackage expectations
        """
        # Parse scenario start time (for matching bar keys)
        start_date = scenario.start_date

        # === STRING INTERNING OPTIMIZATION ===
        symbol_intern = scenario.symbol

        # Filter bars matching this scenario (symbol + start_time)
        scenario_bars = {}
        scenario_counts = {}

        for key, bars_tuple in all_bars_dict.items():
            symbol, timeframe, start_time = key

            # Match criteria: symbol AND start_time must match exactly
            symbol_match = symbol == symbol_intern
            time_match = start_time == start_date

            # Match criteria: symbol AND start_time
            if symbol_match and time_match:
                # Apply string interning to bar dicts
                bars_list = list(bars_tuple)
                for bar in bars_list:
                    bar['symbol'] = symbol_intern
                    bar['timeframe'] = timeframe

                bars_tuple = tuple(bars_list)

                # Store with interned strings
                new_key = (symbol_intern, timeframe, start_time)
                scenario_bars[new_key] = bars_tuple
                scenario_counts[new_key] = all_bar_counts[key]

        return {
            'bars': scenario_bars,
            'counts': scenario_counts
        }

    def prepare_ticks(
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
            self._logger.info(f"📊 Loading ticks for {req.symbol}...")

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

            self._logger.info(
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
            # CRITICAL: No silent fallback! Hard error if start_date not available
            # This error should never occur if Phase 0.5 validation ran correctly

            # Build helpful error message with available range
            first_available = pd.to_datetime(files[0]['start_time'], utc=True)
            last_available = pd.to_datetime(files[-1]['end_time'], utc=True)

            raise ValueError(
                f"No tick data available for {symbol} starting at {start_time}. "
                f"Available data range: {first_available.strftime('%Y-%m-%d %H:%M:%S')} UTC "
                f"→ {last_available.strftime('%Y-%m-%d %H:%M:%S')} UTC. "
                f"This error indicates Phase 0.5 validation was skipped or failed."
            )

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

    def prepare_bars(
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
            self._logger.info(f"📊 Loading bars for {symbol} {timeframe}...")

            # Use manager's API to get bar file
            bar_file = self.bar_index_manager.get_bar_file(symbol, timeframe)

            if bar_file is None:
                self._logger.error(
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
                    self._logger.warning(
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

                self._logger.info(
                    f"  ✅ {len(bars_list)} warmup bars filtered "
                    f"(before {req_start_time})"
                )

        return bars_data, bar_counts
