"""
FiniexTestingIDE - Coverage Report Manager
Generates coverage reports for data quality validation

Responsibilities:
- Load tick index for symbols
- Generate CoverageReport instances
- Cache reports for batch validation
- Phase 0.5: Gap analysis preparation

"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from python.framework.logging.abstract_logger import AbstractLogger
from python.configuration.app_config_manager import AppConfigManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.discoveries.coverage_report import CoverageReport
from python.framework.discoveries.coverage_report_cache import CoverageReportCache
from python.framework.types.coverage_report_types import IndexEntry
from python.framework.types.process_data_types import ProcessDataPackage, RequirementsMap
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import ValidationResult
from python.framework.validators.scenario_data_validator import ScenarioDataValidator


class CoverageReportManager:
    """
    Manages coverage report generation for batch validation.

    Generates gap analysis reports for all symbols in a batch run.
    Reports are used by ScenarioDataValidator to validate data quality.

    """

    def __init__(self,
                 logger: AbstractLogger,
                 scenarios: List[SingleScenario],
                 tick_index_manager: TickIndexManager,
                 app_config: AppConfigManager,
                 use_cache: bool = True):
        """
        Initialize coverage report manager.

        Args:
            logger: Logger instance
            scenarios: List of scenarios
            tick_index_manager: Tick index manager
            app_config: App configuration
            use_cache: Use cache for coverage reports (default: True)
        """
        self._logger = logger
        self._scenarios = scenarios
        self._tick_index_manager = tick_index_manager
        self._coverage_reports: Dict[str, CoverageReport] = {}
        self._app_config = app_config
        self._use_cache = use_cache

        # Initialize cache if enabled
        self._cache: Optional[CoverageReportCache] = None
        if use_cache:
            self._cache = CoverageReportCache(logger=logger)

        # Create validator
        self._validator = None

    def generate_reports(self):
        """Generate coverage reports for all unique (broker_type, symbol) pairs."""
        coverage_reports = {}

        # Get unique (broker_type, symbol) pairs from scenarios
        pairs = set(
            (scenario.data_broker_type, scenario.symbol)
            for scenario in self._scenarios
        )

        # Generate report for each (broker_type, symbol) pair
        for broker_type, symbol in pairs:
            report = self._get_coverage_report(broker_type, symbol)
            if report:
                # Key is tuple (broker_type, symbol)
                coverage_reports[(broker_type, symbol)] = report

        self._logger.info(
            f"✅ Generated {len(coverage_reports)} gap report(s)"
        )

        self._coverage_reports = coverage_reports

        # Create validator
        self._validator = ScenarioDataValidator(
            coverage_reports=self._coverage_reports,
            app_config=self._app_config,
            logger=self._logger
        )

    def _get_coverage_report(self, broker_type: str, symbol: str) -> Optional[CoverageReport]:
        """
        Get coverage report, using cache if available.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol

        Returns:
            CoverageReport or None
        """
        # Use cache if enabled
        if self._cache:
            return self._cache.get_report(broker_type, symbol)

        # Fallback to direct generation via tick index
        return self._tick_index_manager.get_coverage_report(broker_type, symbol)

    def validate_availability(
        self,
        scenarios: List[SingleScenario]
    ):
        """
        Validate data availability BEFORE loading (Phase 0.5).

        Pre-Load Validation:
        - Date logic check (end >= start)
        - Coverage report availability
        - Date range within available data

        Side Effects:
        - Sets scenario.validation_result for ALL scenarios

        Args:
            scenarios: List of scenarios to validate

        Returns:
            Tuple of (valid_scenarios, invalid_scenarios_with_results)
        """
        self._logger.info("🔍 Phase 0.5: Validating data availability...")

        for scenario in scenarios:
            # === STEP 1: Validate date logic (config sanity) ===
            date_logic_errors = self._validator.validate_date_logic(scenario)

            if date_logic_errors:
                # Config error - don't proceed to availability check
                for error in date_logic_errors:
                    self._logger.error(f"❌ {scenario.name}: {error}")

                validation_result = ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=date_logic_errors,
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
                continue

            # === STEP 2: Check coverage report availability ===
            report_key = (scenario.data_broker_type, scenario.symbol)
            report = self._coverage_reports.get(report_key)
            if not report:
                validation_result = ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=[
                        f"No coverage report available for {scenario.data_broker_type}/{scenario.symbol}"],
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
                self._logger.error(
                    f"❌ {scenario.name}: No coverage report for {scenario.data_broker_type}/{scenario.symbol}"
                )
                continue

            # === STEP 3: Validate data availability ===
            availability_errors = self._validator.validate_data_availability(
                scenario, report)

            if availability_errors:
                for error in availability_errors:
                    self._logger.error(f"❌ {scenario.name}: {error}")

                validation_result = ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=availability_errors,
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
            else:
                # All checks passed
                validation_result = ValidationResult(
                    is_valid=True,
                    scenario_name=scenario.name,
                    errors=[],
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)

    def validate_after_load(
        self,
        scenarios: List[SingleScenario],
        scenario_packages: Dict[int, ProcessDataPackage],
        requirements_map: RequirementsMap
    ):
        """
        Validate scenarios after data has been loaded.

        Phase 1.5: Quality validation with loaded data.

        Args:
            scenarios: List of scenarios to validate
            scenario_packages: Dict mapping scenario index to its ProcessDataPackage
            requirements_map: Requirements map for warmup info

        Returns:
            Tuple of (valid_scenarios, invalid_scenarios_with_results)
        """
        self._logger.info("🔍 Phase 1.5: Validating data quality...")

        # Validate all scenarios
        self._validator.validate_loaded_data(
            scenarios=scenarios,
            scenario_packages=scenario_packages,
            requirements_map=requirements_map
        )
