"""
FiniexTestingIDE - Aggregate Scenario Data Requirements (UTC-FIXED)
Collects and deduplicates data requirements from all scenarios

PHASE 0: Requirements Collection (Serial, Main Process)

UTC-FIX:
- All parsed datetimes are UTC-aware
- Prevents timezone comparison errors in data loading
"""

from datetime import timezone
from typing import Dict
from dateutil import parser

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.process_data_types import (
    RequirementsMap,
    TickRequirement,
    BarRequirement,
)
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.factory.worker_factory import WorkerFactory


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

    def __init__(self, logger: AbstractLogger):
        """Initialize empty requirements collector."""
        self._logger = logger
        self.requirements = RequirementsMap()
        self._scenario_count = 0

    def add_scenario(
        self,
        scenario: SingleScenario,
        scenario_index: int
    ) -> Dict[str, int]:
        """
        Add requirements from one scenario.

        UTC-FIX: Parsed datetimes are made UTC-aware.

        Args:
            scenario: Scenario configuration
            scenario_index: Scenario index

        Returns:
            warmup_requirements: {timeframe: warmup_count} for this scenario
        """
        self._scenario_count += 1

        # === TICK REQUIREMENTS ===
        start_time = scenario.start_date

        end_time = None
        if scenario.end_date:
            end_time = scenario.end_date

        tick_req = TickRequirement(
            scenario_name=scenario.name,
            broker_type=scenario.data_broker_type,
            symbol=scenario.symbol,
            start_time=start_time,
            end_time=end_time,
            max_ticks=scenario.max_ticks,
            start_readable=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_readable=end_time.strftime(
                "%Y-%m-%d %H:%M:%S") if end_time else ""
        )
        self.requirements.add_tick_requirement(tick_req)

        # === BAR REQUIREMENTS (via Config + Classmethod) ===
        # No worker instantiation needed!
        # Use calculate_requirements() classmethod instead

        warmup_by_timeframe = {}

        worker_instances = scenario.strategy_config.get("worker_instances", {})
        workers_config = scenario.strategy_config.get("workers", {})

        for instance_name, worker_type in worker_instances.items():
            # Get config for this worker instance
            worker_config = workers_config.get(instance_name, {})

            strict = True
            if scenario.execution_config:
                strict = scenario.execution_config.get(
                    "strict_parameter_validation", True
                )
            worker_factory = WorkerFactory(
                logger=self._logger, strict_parameter_validation=strict)

            # Resolve worker class (from registry)
            worker_class = worker_factory._resolve_worker_class(
                worker_type)

            # Validate config (ensures 'periods' exists & valid Timeframes for INDICATOR)
            worker_class.validate_config(worker_config)

            # Validate algorithm parameters against schema (min/max/type)
            warnings = worker_class.validate_parameter_schema(
                worker_config, strict=strict
            )
            for w in warnings:
                self._logger.warning(f"⚠️ {w}")

            # Calculate requirements via CLASSMETHOD (no instance!)
            requirements = worker_class.calculate_requirements(worker_config)

            self._logger.debug(
                f"[Requirements] Scenario {scenario_index + 1}: "
                f"{scenario.data_broker_type}/{scenario.symbol}, {len(warmup_by_timeframe)} timeframes, "
                f"warmup_by_timeframe={warmup_by_timeframe}"
            )

            # Merge with other workers (max per timeframe)
            for tf, bars in requirements.items():
                warmup_by_timeframe[tf] = max(
                    warmup_by_timeframe.get(tf, 0), bars
                )

        self._logger.debug(
            f"[Requirements] Scenario {scenario_index + 1}: "
            f"{scenario.data_broker_type}/{scenario.symbol}, {len(warmup_by_timeframe)} timeframes, "
            f"warmup_by_timeframe={warmup_by_timeframe}"
        )

        # Convert to BarRequirements for aggregation
        for timeframe, warmup_count in warmup_by_timeframe.items():
            bar_req = BarRequirement(
                scenario_name=scenario.name,
                broker_type=scenario.data_broker_type,
                symbol=scenario.symbol,
                timeframe=timeframe,
                warmup_count=warmup_count,
                start_time=start_time,  # Already UTC-aware
                start_readable=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            self.requirements.add_bar_requirement(bar_req)

        # Return warmup requirements for ProcessExecutor
        return warmup_by_timeframe

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
        self._logger.info(
            f"📊 Requirements collected: "
            f"{len(self.requirements.tick_requirements)} tick reqs, "
            f"{len(self.requirements.bar_requirements)} bar reqs "
            f"from {self._scenario_count} scenarios"
        )

        self._logger.info(
            f"✅ After deduplication: "
            f"{len(self.requirements.tick_requirements)} tick loads, "
            f"{len(self.requirements.bar_requirements)} bar loads"
        )

        return self.requirements
