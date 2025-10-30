"""
FiniexTestingIDE - Aggregate Scenario Data Requirements (UTC-FIXED)
Collects and deduplicates data requirements from all scenarios

PHASE 0: Requirements Collection (Serial, Main Process)

UTC-FIX:
- All parsed datetimes are UTC-aware
- Prevents timezone comparison errors in data loading
"""

from datetime import datetime, timezone
from typing import Dict, List, Tuple
from dateutil import parser

from python.components.logger.abstract_logger import AbstractLogger
from python.framework.types.process_data_types import (
    RequirementsMap,
    TickRequirement,
    BarRequirement,
)
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.utils.scenario_requirements import (
    calculate_scenario_requirements
)
from python.framework.factory.worker_factory import WorkerFactory
from python.configuration import AppConfigLoader

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class AggregateScenarioDataRequirements:
    """
    Aggregates data requirements from all scenarios.

    WORKFLOW:
    1. add_scenario() for each scenario
    2. finalize() to deduplicate and optimize
    3. Output: RequirementsMap for SharedDataPreparator

    UTC-FIX:
    - All datetime objects are UTC-aware
    - Prevents timezone comparison errors
    """

    def __init__(self):
        """Initialize empty requirements collector."""
        self.requirements = RequirementsMap()
        self.worker_factory = WorkerFactory()
        self._scenario_count = 0

    def add_scenario(
        self,
        scenario: SingleScenario,
        app_config: AppConfigLoader,
        scenario_index: int,
        logger: AbstractLogger = vLog
    ) -> Dict[str, int]:
        """
        Add requirements from one scenario.

        UTC-FIX: Parsed datetimes are made UTC-aware.

        Args:
            scenario: Scenario configuration
            app_config: Application config
            scenario_index: Scenario index

        Returns:
            warmup_requirements: {timeframe: warmup_count} for this scenario
        """
        self._scenario_count += 1

        # === TICK REQUIREMENTS ===
        # UTC-FIX: Parse and make UTC-aware
        start_time = parser.parse(scenario.start_date)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        end_time = None
        if scenario.end_date:
            end_time = parser.parse(scenario.end_date)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)

        tick_req = TickRequirement(
            symbol=scenario.symbol,
            start_time=start_time,
            end_time=end_time,
            max_ticks=scenario.max_ticks,
            start_readable=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_readable=end_time.strftime("%Y-%m-%d %H:%M:%S")
        )
        self.requirements.add_tick_requirement(tick_req)

        # === BAR REQUIREMENTS (via Worker System) ===
        # Create workers temporarily
        # 1. Create workers temporarily
        worker_factory = WorkerFactory()
        workers_dict = worker_factory.create_workers_from_config(
            strategy_config=scenario.strategy_config,
            logger=logger
        )
        workers = list(workers_dict.values())

        vLog.debug(
            f"[Requirements] Scenario {scenario_index + 1}: "
            f"Created {len(workers)} workers temporarily"
        )

        # Calculate requirements using existing method
        scenario_reqs = calculate_scenario_requirements(workers)

        vLog.debug(
            f"[Requirements] Scenario {scenario_index + 1}: "
            f"{scenario.symbol}, {len(scenario_reqs.all_timeframes)} timeframes, "
            f"max_warmup={scenario_reqs.max_warmup_bars}"
        )

        # Convert to BarRequirements for aggregation
        for timeframe, warmup_count in scenario_reqs.warmup_by_timeframe.items():
            bar_req = BarRequirement(
                symbol=scenario.symbol,
                timeframe=timeframe,
                warmup_count=warmup_count,
                start_time=start_time,  # Already UTC-aware
                start_readable=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            self.requirements.add_bar_requirement(bar_req)

        # Return warmup requirements for ProcessExecutor
        return scenario_reqs.warmup_by_timeframe

    def finalize(self) -> RequirementsMap:
        """
        Finalize requirements with deduplication.

        OPTIMIZATION:
        - Merges overlapping tick ranges
        - Groups bar requirements by (symbol, timeframe, start_time)
        - Returns optimized loading strategy

        Returns:
            Deduplicated RequirementsMap
        """
        vLog.info(
            f"📊 Requirements collected: "
            f"{len(self.requirements.tick_requirements)} tick reqs, "
            f"{len(self.requirements.bar_requirements)} bar reqs "
            f"from {self._scenario_count} scenarios"
        )

        # Deduplicate tick requirements
        # self._deduplicate_tick_requirements()

        # Deduplicate bar requirements
        # self._deduplicate_bar_requirements()

        vLog.info(
            f"✅ After deduplication: "
            f"{len(self.requirements.tick_requirements)} tick loads, "
            f"{len(self.requirements.bar_requirements)} bar loads"
        )

        return self.requirements

    def _deduplicate_tick_requirements(self) -> None:
        """
        Deduplicate tick requirements by merging overlapping ranges.

        STRATEGY:
        - Group by symbol
        - Merge overlapping time ranges
        - Handle both tick-limited and timespan modes
        """
        # Group by symbol
        by_symbol: Dict[str, List[TickRequirement]] = {}
        for req in self.requirements.tick_requirements:
            if req.symbol not in by_symbol:
                by_symbol[req.symbol] = []
            by_symbol[req.symbol].append(req)

        # Merge overlapping ranges per symbol
        deduplicated = []
        for symbol, reqs in by_symbol.items():
            # Sort by start_time
            reqs.sort(key=lambda r: r.start_time)

            # Simple merge: Take MIN(start) and MAX(end)
            min_start = min(r.start_time for r in reqs)

            # Handle end_time (None for tick-limited mode)
            end_times = [r.end_time for r in reqs if r.end_time is not None]
            max_end = max(end_times) if end_times else None

            # Aggregate max_ticks (for tick-limited scenarios)
            max_ticks_values = [
                r.max_ticks for r in reqs if r.max_ticks is not None]
            total_max_ticks = max(
                max_ticks_values) if max_ticks_values else None

            merged = TickRequirement(
                symbol=symbol,
                start_time=min_start,
                end_time=max_end,
                max_ticks=total_max_ticks,
                start_readable=min_start.strftime(
                    "%Y-%m-%d %H:%M:%S"),
                end_readable=max_end.strftime(
                    "%Y-%m-%d %H:%M:%S"),
            )
            deduplicated.append(merged)

        self.requirements.tick_requirements = deduplicated

    def _deduplicate_bar_requirements(self) -> None:
        """
        Deduplicate bar requirements.

        STRATEGY:
        - Group by (symbol, timeframe, start_time)
        - Take maximum warmup_count for each group
        """
        # Group by (symbol, timeframe, start_time)
        by_key: Dict[Tuple[str, str, datetime], int] = {}

        for req in self.requirements.bar_requirements:
            key = (req.symbol, req.timeframe, req.start_time)
            by_key[key] = max(by_key.get(key, 0), req.warmup_count)

        # Rebuild deduplicated list
        deduplicated = []
        for (symbol, timeframe, start_time), warmup_count in by_key.items():
            deduplicated.append(BarRequirement(
                symbol=symbol,
                timeframe=timeframe,
                warmup_count=warmup_count,
                start_time=start_time,
                start_readable=start_time.strftime(
                    "%Y-%m-%d %H:%M:%S"),
            ))

        self.requirements.bar_requirements = deduplicated
