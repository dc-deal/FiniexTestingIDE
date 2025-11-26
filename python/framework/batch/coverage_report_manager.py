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
from typing import Dict, List, Tuple

from python.components.logger.abstract_logger import AbstractLogger
from python.configuration.app_config_manager import AppConfigManager
from python.data_worker.data_loader.tick_index_manager import TickIndexManager
from python.framework.reporting.coverage_report import CoverageReport
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
                 app_config: AppConfigManager):
        """
        Initialize coverage report manager.

        Args:
            data_dir: Root data directory (e.g., 'data/parquet')
            logger: Logger instance
        """
        self._logger = logger
        self._scenarios = scenarios
        self._tick_index_manager = tick_index_manager
        self._coverage_reports: Dict[str, CoverageReport] = {}
        self._app_config = app_config

        # Create validator
        self._validator = None

    def generate_reports(self):
        """
        Generate coverage reports for all symbols.

        Args:
            symbols: Set of unique symbol names

        Returns:
            Dict mapping symbol to CoverageReport
        """
        coverage_reports = {}

        # Get unique symbols from scenarios
        symbols = set(
            scenario.symbol for scenario in self._scenarios)

        # Generate report for each symbol
        for symbol in symbols:
            report = self._tick_index_manager.get_coverage_report(
                symbol)
            if report:
                coverage_reports[symbol] = report

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

    def validate_availability(
        self,
        scenarios: List[SingleScenario]
    ) -> Tuple[List[SingleScenario], List[Tuple[SingleScenario, ValidationResult]]]:
        """
        Validate data availability BEFORE loading (Phase 0.5).

        Pre-Load Validation:
        - Date logic check (end >= start)
        - Coverage report availability
        - Date range within available data

        Side Effects:
        - Sets scenario.validation_result for ALL scenarios
        - Invalid scenarios get is_valid() = False

        Args:
            scenarios: List of scenarios to validate

        Returns:
            Tuple of (valid_scenarios, invalid_scenarios_with_results)
        """
        self._logger.info("🔍 Phase 0.5: Validating data availability...")

        valid_scenarios = []
        invalid_scenarios = []

        for scenario in scenarios:
            if not scenario.is_valid():
                # perhaps something went wrong before---
                invalid_scenarios.append(
                    (scenario, scenario.validation_result))
                continue
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
                invalid_scenarios.append((scenario, validation_result))
                continue

            # === STEP 2: Check coverage report availability ===
            report = self._coverage_reports.get(scenario.symbol)
            if not report:
                validation_result = ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=[
                        f"No coverage report available for {scenario.symbol}"],
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
                invalid_scenarios.append((scenario, validation_result))
                self._logger.error(
                    f"❌ {scenario.name}: No coverage report for {scenario.symbol}"
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
                invalid_scenarios.append((scenario, validation_result))
            else:
                # All checks passed
                validation_result = ValidationResult(
                    is_valid=True,
                    scenario_name=scenario.name,
                    errors=[],
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
                valid_scenarios.append((scenario, validation_result))

        if invalid_scenarios:
            self._logger.warning(
                f"⚠️  {len(invalid_scenarios)} scenario(s) failed availability check - skipped"
            )

        self._logger.info(
            f"✅ Availability check: {len(valid_scenarios)}/{len(scenarios)} scenarios valid"
        )

        return valid_scenarios, invalid_scenarios

    def validate_after_load(
        self,
        scenarios: List[SingleScenario],
        shared_data: ProcessDataPackage,
        requirements_map: RequirementsMap
    ) -> Tuple[List[SingleScenario], List[Tuple[SingleScenario, ValidationResult]]]:
        """
        Validate scenarios after data has been loaded.

        Phase 1.5: Quality validation with loaded data.

        Args:
            scenarios: List of scenarios to validate
            shared_data: Loaded tick and bar data
            requirements_map: Requirements map for warmup info

        Returns:
            Tuple of (valid_scenarios, invalid_scenarios_with_results)
        """
        self._logger.info("🔍 Phase 1.5: Validating data quality...")

        # Validate all scenarios
        valid_scenarios, invalid_scenarios, = self._validator.validate_loaded_data(
            scenarios=scenarios,
            shared_data=shared_data,
            requirements_map=requirements_map
        )
        if invalid_scenarios:
            self._logger.warning(
                f"⚠️  {len(invalid_scenarios)} scenario(s) failed validation - skipped"
            )

        self._logger.info(
            f"✅ Validation complete: {len(valid_scenarios)}/{len(scenarios)} scenarios valid"
        )

        return valid_scenarios, invalid_scenarios
