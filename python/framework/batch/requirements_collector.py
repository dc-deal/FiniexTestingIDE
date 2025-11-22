"""
FiniexTestingIDE - Requirements Collector
Phase 0: Collects data requirements from all scenarios

Extracted from BatchOrchestrator to separate requirement collection logic.
"""
from python.framework.data_preperation.aggregate_scenario_data_requirements import AggregateScenarioDataRequirements
from python.framework.types.process_data_types import RequirementsMap
from python.framework.types.scenario_set_types import SingleScenario
from python.configuration import AppConfigManager
from python.components.logger.abstract_logger import AbstractLogger
from typing import List, Dict


class RequirementsCollector:
    """
    Collects and aggregates data requirements from multiple scenarios.

    Responsibilities:
    - Iterate through all scenarios
    - Collect warmup requirements per scenario
    - Aggregate requirements into unified map
    - Return finalized requirements for data preparation
    """

    def __init__(self, logger: AbstractLogger):
        """
        Initialize requirements collector.

        Args:
            logger: Logger instance for status messages
        """
        self._logger = logger
        self._aggregate_requirements = AggregateScenarioDataRequirements()
        self._warmup_requirements_by_scenario: Dict[int, Dict] = {}

    def collect(
        self,
        scenarios: List[SingleScenario],
        app_config: AppConfigManager
    ) -> tuple[RequirementsMap, Dict[int, Dict]]:
        """
        Collect requirements from all scenarios.

        Args:
            scenarios: List of scenarios to analyze
            app_config: Application configuration manager

        Returns:
            Tuple of (requirements_map, warmup_requirements_by_scenario)
        """
        self._logger.info("📋 Phase 0: Collecting data requirements...")

        # Collect requirements from each scenario
        for idx, scenario in enumerate(scenarios):
            warmup_reqs = self._aggregate_requirements.add_scenario(
                scenario=scenario,
                app_config=app_config,
                scenario_index=idx,
                logger=self._logger
            )
            self._warmup_requirements_by_scenario[idx] = warmup_reqs

        # Finalize and return
        requirements_map = self._aggregate_requirements.finalize()

        return requirements_map, self._warmup_requirements_by_scenario
