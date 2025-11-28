"""
FiniexTestingIDE - Requirements Collector
Phase 0: Collects data requirements from all scenarios

Extracted from BatchOrchestrator to separate requirement collection logic.
"""
import traceback
from python.framework.data_preperation.aggregate_scenario_data_requirements import AggregateScenarioDataRequirements
from python.framework.types.process_data_types import RequirementsMap
from python.framework.types.scenario_set_types import SingleScenario
from python.components.logger.abstract_logger import AbstractLogger
from typing import List, Dict

from python.framework.types.validation_types import ValidationResult


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
        self._aggregate_requirements = AggregateScenarioDataRequirements(
            logger)
        self._warmup_requirements_by_scenario: Dict[int, Dict] = {}

    def collect_and_validate(
        self,
        scenarios: List[SingleScenario],
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
            # check if scenario is already infalid, prevent unnessecary follow up errors.
            if not scenario.is_valid():
                self._logger.warning(
                    f"⚠️  Collection of Scenario {idx+1}/{len(scenarios)}: "
                    f"{scenario.name} - SKIPPED (validation failed)"
                )
                # assume no requirements.
                self._warmup_requirements_by_scenario[idx] = {}
                continue
            try:
                warmup_reqs = self._aggregate_requirements.add_scenario(
                    scenario=scenario,
                    scenario_index=idx
                )
            except Exception as e:
                # Config error - don't proceed to availability check
                error_formatted = f"❌ {scenario.name}: Error - {e} \n{traceback.format_exc()}"
                self._logger.error(error_formatted)

                validation_result = ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=[error_formatted],
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
                continue

            self._warmup_requirements_by_scenario[idx] = warmup_reqs

        # Finalize and return
        requirements_map = self._aggregate_requirements.finalize()

        return requirements_map, self._warmup_requirements_by_scenario
