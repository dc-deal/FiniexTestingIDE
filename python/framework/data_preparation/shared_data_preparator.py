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
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.process_data_types import (
    ClippingStats,
    ProcessDataPackage,
    RequirementsMap,
    TickRequirement,
    BarRequirement
)
from python.data_management.index.tick_index_manager import TickIndexManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import ValidationResult
from python.framework.utils.process_serialization_utils import serialize_ticks_for_transport, time_range_from_transport_ticks
from python.framework.utils.time_utils import ensure_utc_aware


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

    def __init__(self, logger: ScenarioLogger):
        """
        Initialize data preparator with index managers.
        """
        self._logger = logger

        # Cache for pre-converted file timestamps: (broker_type, symbol) →
        # List[Tuple[Timestamp, Timestamp, str]] (start, end, version)
        # Avoids repeated pd.to_datetime calls in _collect_parquet_versions (O(n_scenarios×n_files) → O(n_files))
        self._file_ts_cache: Dict[Tuple[str, str], List[Tuple[Any, Any, str]]] = {}

        # Use existing index managers
        self._logger.debug("📚 Initializing index managers...")

        # Tick index manager
        self.tick_index_manager = TickIndexManager(self._logger)
        self.tick_index_manager.build_index()  # Auto-loads or rebuilds

        # Bar index manager
        self.bar_index_manager = BarsIndexManager(self._logger)
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
    ) -> Tuple[Dict[int, ProcessDataPackage], Dict[int, ClippingStats]]:
        """
        Prepare scenario-specific data packages (OPTIMIZATION).

        Instead of one global package (61 MB), creates individual packages
        per scenario (3-5 MB each). Reduces ProcessPool pickle overhead by 5x.

        Workflow:
        1. Load ALL data once (memory-efficient, no duplication)
        2. Filter data per scenario (symbol + time range)
        3. Apply tick processing budget (clipping simulation)
        4. Package with string interning (symbol deduplication)

        Args:
            requirements_map: Aggregated requirements from all scenarios
            scenarios: List of scenarios to prepare packages for
            broker_configs: Pre-loaded broker configurations

        Returns:
            Tuple of:
            - Dict mapping scenario_index → ProcessDataPackage
            - Dict mapping scenario_index → ClippingStats (empty if budget disabled)
            Invalid scenarios are skipped (no entry in dicts)
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
        clipping_stats_map = {}

        for idx, scenario in enumerate(scenarios):
            scenario_index = scenario.scenario_index
            # Filter ticks for this scenario
            scenario_ticks = self._filter_ticks_for_scenario(
                scenario, all_ticks_dict, all_tick_counts, all_tick_ranges
            )
            if (scenario_ticks is None):
                continue

            # Apply tick processing budget (clipping simulation)
            budget_ms = scenario.execution_config.get(
                'tick_processing_budget_ms', 0.0
            ) if scenario.execution_config else 0.0

            if budget_ms > 0:
                scenario_ticks, clipping = self._apply_tick_budget(
                    scenario_ticks, scenario.symbol, budget_ms
                )
                clipping_stats_map[scenario_index] = clipping
                if clipping.ticks_clipped > 0:
                    self._logger.info(
                        f"✂️  Budget {budget_ms}ms → {scenario.name}: "
                        f"{clipping.ticks_clipped:,}/{clipping.ticks_total:,} "
                        f"clipped ({clipping.clipping_rate_pct:.1f}%)"
                    )

            # Filter bars for this scenario
            scenario_bars = self._filter_bars_for_scenario(
                scenario, all_bars_dict, all_bar_counts
            )

            # Create scenario-specific package
            scenario_packages[scenario_index] = ProcessDataPackage(
                ticks=scenario_ticks['ticks'],
                bars=scenario_bars['bars'],
                broker_configs=broker_configs,
                tick_counts=scenario_ticks['counts'],
                tick_ranges=scenario_ticks['ranges'],
                bar_counts=scenario_bars['counts']
            )

            # Collect data_format_versions from actually loaded Parquet files
            tick_range = scenario_ticks['ranges'].get(scenario.symbol)
            scenario.data_format_versions = self._collect_parquet_versions(
                scenario.data_broker_type, scenario.symbol, tick_range
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

        return scenario_packages, clipping_stats_map

    def _collect_parquet_versions(
        self,
        broker_type: str,
        symbol: str,
        tick_range: Optional[Tuple[datetime, datetime]] = None
    ) -> List[str]:
        """
        Collect data_format_version from Parquet files that overlap the loaded time range.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            tick_range: (first_tick, last_tick) of actually loaded data, None = all files

        Returns:
            List of version strings from matching Parquet files
        """
        if broker_type not in self.tick_index_manager.index:
            return []
        if symbol not in self.tick_index_manager.index[broker_type]:
            return []

        files = self.tick_index_manager.index[broker_type][symbol]

        if tick_range is None:
            return [f.get('data_format_version', 'unknown') for f in files]

        # Pre-convert file timestamps once per (broker_type, symbol).
        # pd.to_datetime on a single string is ~150-200µs — calling it N_scenarios × N_files
        # times blows up to 10-20s for large symbol sets. Cache eliminates all repeated calls.
        cache_key = (broker_type, symbol)
        if cache_key not in self._file_ts_cache:
            self._file_ts_cache[cache_key] = [
                (
                    pd.to_datetime(f['start_time'], utc=True),
                    pd.to_datetime(f['end_time'], utc=True),
                    f.get('data_format_version', 'unknown')
                )
                for f in files
            ]
        file_ranges = self._file_ts_cache[cache_key]

        # File overlaps if it starts before range ends AND ends after range starts
        range_start, range_end = tick_range
        return [
            version
            for file_start, file_end, version in file_ranges
            if file_start <= range_end and file_end >= range_start
        ]

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
            error_message = f"No tick data found for scenario '{scenario_name}' " + \
                f"(available: {list(all_ticks_dict.keys())})"
            validation_error = ValidationResult(
                is_valid=False,
                scenario_name=scenario.name,
                errors=[error_message],
                warnings=[]
            )
            scenario.validation_result.append(validation_error)
            return None

        ticks_tuple = all_ticks_dict[scenario_name]

        # Return as dict with single key (scenario symbol)
        # Structure: Dict[str, Tuple] matches ProcessDataPackage.ticks
        # NOTE: symbol is NOT included in TickTransportColumn — deserialization
        # takes symbol from scenario_symbol (scenario config), not from dicts.
        return {
            'ticks': {scenario.symbol: ticks_tuple},
            'counts': {scenario.symbol: all_tick_counts[scenario_name]},
            'ranges': {scenario.symbol: all_tick_ranges[scenario_name]}
        }

    def _apply_tick_budget(
        self,
        scenario_ticks: Dict[str, Any],
        symbol: str,
        budget_ms: float
    ) -> Tuple[Dict[str, Any], ClippingStats]:
        """
        Apply tick processing budget flagging (deterministic clipping simulation).

        Virtual clock advances by budget_ms after each processed tick.
        Ticks arriving before the virtual clock expires are flagged as clipped.
        All ticks are kept — the flag controls whether the algo path processes them.
        The broker path (trade_simulator) always sees every tick.

        Args:
            scenario_ticks: Filtered tick data dict from _filter_ticks_for_scenario
            symbol: Scenario symbol
            budget_ms: Processing budget in milliseconds

        Returns:
            Tuple of (flagged scenario_ticks dict, ClippingStats)
        """
        ticks_tuple = scenario_ticks['ticks'].get(symbol, ())
        ticks_total = len(ticks_tuple)

        if ticks_total == 0:
            return scenario_ticks, ClippingStats(budget_ms=budget_ms)

        # Check if collected_msc is available (V1.3.0+ data)
        first_tick = ticks_tuple[0]
        if first_tick.get('collected_msc', 0) == 0:
            self._logger.warning(
                f"⚠️  Budget filtering skipped for {symbol}: "
                f"collected_msc not available (pre-V1.3.0 data)"
            )
            return scenario_ticks, ClippingStats(
                ticks_total=ticks_total,
                ticks_kept=ticks_total,
                budget_ms=budget_ms
            )

        # Virtual clock flagging — all ticks kept, clipped ones flagged
        virtual_clock = 0.0
        flagged_ticks = []
        ticks_kept = 0

        for tick in ticks_tuple:
            tick_copy = dict(tick)
            collected_msc = tick_copy['collected_msc']
            if collected_msc >= virtual_clock:
                tick_copy['is_clipped'] = False
                virtual_clock = collected_msc + budget_ms
                ticks_kept += 1
            else:
                tick_copy['is_clipped'] = True
            flagged_ticks.append(tick_copy)

        ticks_clipped = ticks_total - ticks_kept
        clipping_rate = (ticks_clipped / ticks_total * 100) if ticks_total > 0 else 0.0

        # Return all ticks with is_clipped flags — counts reflect full dataset
        flagged_result = {
            'ticks': {symbol: tuple(flagged_ticks)},
            'counts': {symbol: ticks_total},
            'ranges': scenario_ticks['ranges']
        }

        return flagged_result, ClippingStats(
            ticks_total=ticks_total,
            ticks_kept=ticks_kept,
            ticks_clipped=ticks_clipped,
            clipping_rate_pct=round(clipping_rate, 2),
            budget_ms=budget_ms
        )

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

        OPTIMIZATION: Groups requirements by (broker_type, symbol) and loads each
        symbol's Parquet data once into RAM, then filters per scenario. Replaces
        the previous per-scenario Parquet read (O(n_scenarios) → O(n_symbols) reads).

        NOTE (#21 — Memory Manager): This is a run-scoped in-memory load. When #21
        implements a persistent file-level cache (get_or_load), replace the
        pd.concat([pd.read_parquet(f)...]) block in STEP 3 with cache lookups.
        The grouping logic (STEP 1+2+4) stays unchanged.

        Args:
            requirements: List of tick requirements

        Returns:
            (ticks_data, tick_counts, tick_ranges)
            Failed scenarios are skipped (not included in dicts)
        """
        ticks_data = {}
        tick_counts = {}
        tick_ranges = {}

        # === STEP 1: Group requirements by (broker_type, symbol) ===
        # All scenarios for the same symbol share one Parquet load.
        by_broker_symbol: Dict[Tuple[str, str], List[TickRequirement]] = {}
        for req in requirements:
            key = (req.broker_type, req.symbol)
            if key not in by_broker_symbol:
                by_broker_symbol[key] = []
            by_broker_symbol[key].append(req)

        # === STEP 2+3: Load each symbol once into RAM, filter per scenario ===
        for (broker_type, symbol), reqs in by_broker_symbol.items():
            self._logger.info(
                f"📥 Loading ticks for {broker_type}/{symbol} "
                f"({len(reqs)} scenario(s))..."
            )

            # Validate index entries before doing any IO
            if broker_type not in self.tick_index_manager.index:
                self._logger.error(
                    f"❌ Broker type '{broker_type}' not found in tick index"
                )
                continue
            if symbol not in self.tick_index_manager.index[broker_type]:
                self._logger.error(
                    f"❌ Symbol '{symbol}' not found in tick index for broker_type '{broker_type}'"
                )
                continue

            # Compute union time range across all requirements for this symbol.
            # This is the minimal range that covers every scenario — only relevant
            # Parquet files will be read (tick_index_manager.get_relevant_files).
            union_start = ensure_utc_aware(min(r.start_time for r in reqs))
            end_times = [r.end_time for r in reqs if r.end_time is not None]
            if end_times:
                # At least one timespan-mode requirement — use max end_time
                union_end = ensure_utc_aware(max(end_times))
            else:
                # All requirements are max_ticks mode (no end_time) —
                # use the last file's end as conservative upper bound
                files = self.tick_index_manager.index[broker_type][symbol]
                union_end = pd.to_datetime(files[-1]['end_time'], utc=True)

            relevant_files = self.tick_index_manager.get_relevant_files(
                broker_type=broker_type,
                symbol=symbol,
                start_date=union_start,
                end_date=union_end
            )
            if not relevant_files:
                self._logger.error(
                    f"❌ No tick files found for {broker_type}/{symbol} "
                    f"in range {union_start} - {union_end}"
                )
                continue

            # Load and concat all relevant files into one in-memory DataFrame
            dfs = []
            for file_path in relevant_files:
                df = pd.read_parquet(file_path)
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
                dfs.append(df)
            full_df = pd.concat(dfs).sort_values('timestamp').reset_index(drop=True)

            self._logger.info(
                f"  ✅ {len(full_df):,} ticks in RAM from {len(relevant_files)} file(s) "
                f"({union_start} → {union_end})"
            )

            # === STEP 4: Filter per requirement using searchsorted (O(log n)) ===
            # Pre-extract timestamp Series once — searchsorted avoids O(n) boolean scan
            timestamps = full_df['timestamp']

            for req in reqs:
                req_start = ensure_utc_aware(req.start_time)
                start_idx = timestamps.searchsorted(req_start, side='left')

                if req.max_ticks is not None:
                    # Tick-limited mode: direct iloc slice — no boolean scan
                    filtered_df = full_df.iloc[start_idx:start_idx + req.max_ticks]
                else:
                    # Timespan mode: binary search for end boundary too
                    req_end = ensure_utc_aware(req.end_time)
                    end_idx = timestamps.searchsorted(req_end, side='right')
                    filtered_df = full_df.iloc[start_idx:end_idx]

                ticks = serialize_ticks_for_transport(filtered_df)

                if not ticks:
                    self._logger.warning(
                        f"⚠️  Skipping scenario '{req.scenario_name}' - no ticks after filtering"
                    )
                    continue

                time_range = time_range_from_transport_ticks(ticks)

                # Store as tuple (immutable, CoW-friendly)
                ticks_data[req.scenario_name] = tuple(ticks)
                tick_counts[req.scenario_name] = len(ticks)
                tick_ranges[req.scenario_name] = time_range

                self._logger.info(
                    f"  ✅ {len(ticks):,} ticks filtered for '{req.scenario_name}' "
                    f"({time_range[0]} → {time_range[1]})"
                )

        return ticks_data, tick_counts, tick_ranges

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
        by_broker_symbol_tf: Dict[Tuple[str,
                                        str, str], List[BarRequirement]] = {}

        for req in requirements:
            key = (req.broker_type, req.symbol, req.timeframe)
            if key not in by_broker_symbol_tf:
                by_broker_symbol_tf[key] = []
            by_broker_symbol_tf[key].append(req)

        # Load and filter for each (broker_type, symbol, timeframe)
        for (broker_type, symbol, timeframe), reqs in by_broker_symbol_tf.items():
            self._logger.info(
                f"📊 Loading bars for {broker_type}/{symbol} {timeframe}...")

            # Use manager's API to get bar file
            bar_file = self.bar_index_manager.get_bar_file(
                broker_type, symbol, timeframe)

            if bar_file is None:
                self._logger.error(
                    f"Bar file not found for {broker_type}/{symbol} {timeframe} - "
                    f"available timeframes: {self.bar_index_manager.get_available_timeframes(broker_type, symbol)}"
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
                req_start_time = ensure_utc_aware(req_start_time)

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
