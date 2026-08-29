"""
FiniexTestingIDE - Coverage Report Manager
Generates coverage reports for data quality validation

Responsibilities:
- Generate DataCoverageReport instances
- Cache reports for batch validation
- Phase 0.5: Gap analysis preparation

"""

from typing import Dict, List, Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.discoveries.data_coverage.data_coverage_report_cache import (
    DataCoverageReportCache,
)
from python.framework.discoveries.signal_coverage.signal_coverage_report import SignalCoverageReport
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.process_data_types import ProcessDataPackage, RequirementsMap
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
    ValidationResult,
)
from python.framework.validators.scenario_data_validator import ScenarioDataValidator


class DataCoverageReportManager:
    """
    Manages coverage report generation for batch validation.

    Generates gap analysis reports for all symbols in a batch run.
    Reports are used by ScenarioDataValidator to validate data quality.

    """

    def __init__(self,
                 logger: AbstractLogger,
                 scenarios: List[SingleScenario],
                 app_config: AppConfigManager,
                 use_cache: bool = True,
                 signal_coverage_reports: Dict[Tuple[str, str], SignalCoverageReport] = None):
        """
        Initialize coverage report manager.

        Args:
            logger: Logger instance
            scenarios: List of scenarios
            app_config: App configuration
            use_cache: Use cache for coverage reports (default: True)
            signal_coverage_reports: Signal coverage reports keyed by
                (data_sentiment_type, symbol) — handed to the validator alongside
                the tick reports; empty when no scenario binds a signal source
        """
        self._logger = logger
        self._scenarios = scenarios
        self._data_coverage_reports: Dict[str, DataCoverageReport] = {}
        self._signal_coverage_reports = signal_coverage_reports or {}
        self._app_config = app_config

        # Initialize cache if enabled
        self._cache: Optional[DataCoverageReportCache] = None
        if use_cache:
            self._cache = DataCoverageReportCache(logger=logger)

        # Create validator
        self._validator = None

    def generate_reports(self):
        """Generate coverage reports for all unique (broker_type, symbol) pairs."""
        data_coverage_reports = {}

        # Get unique (broker_type, symbol) pairs from scenarios
        pairs = set(
            (scenario.data_broker_type, scenario.symbol)
            for scenario in self._scenarios
        )

        # Generate report for each (broker_type, symbol) pair
        for broker_type, symbol in pairs:
            report = self._get_data_coverage_report(broker_type, symbol)
            if report:
                # Key is tuple (broker_type, symbol)
                data_coverage_reports[(broker_type, symbol)] = report

        self._logger.info(
            f'✅ Generated {len(data_coverage_reports)} gap report(s)'
        )

        self._data_coverage_reports = data_coverage_reports

        # Create validator
        self._validator = ScenarioDataValidator(
            data_coverage_reports=self._data_coverage_reports,
            app_config=self._app_config,
            logger=self._logger,
            signal_coverage_reports=self._signal_coverage_reports
        )

    def _get_data_coverage_report(self, broker_type: str, symbol: str) -> Optional[DataCoverageReport]:
        """
        Get coverage report, using cache if available.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol

        Returns:
            DataCoverageReport or None
        """
        # Use cache if enabled
        if self._cache:
            return self._cache.get_report(broker_type, symbol)

        # Fallback to direct generation
        report = DataCoverageReport(symbol=symbol, broker_type=broker_type)
        report.analyze()
        return report

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
        self._logger.info('🔍 Phase 0.5: Validating data availability...')

        for scenario in scenarios:
            # === STEP 1: Validate date logic (config sanity) ===
            date_logic_errors = self._validator.validate_date_logic(scenario)

            if date_logic_errors:
                # Config error - don't proceed to availability check
                for error in date_logic_errors:
                    self._logger.error(f'❌ {scenario.name}: {error}')

                validation_result = ValidationResult(scenario.name, [
                    ValidationFinding(
                        severity=Severity.ERROR, check='date_logic',
                        domain=ValidationDomain.CONFIG,
                        message=error, scope=scenario.name)
                    for error in date_logic_errors])
                scenario.validation_result.append(validation_result)
                continue

            # === STEP 2: Check coverage report availability ===
            report_key = (scenario.data_broker_type, scenario.symbol)
            report = self._data_coverage_reports.get(report_key)
            if not report:
                validation_result = ValidationResult(scenario.name, [ValidationFinding(
                    severity=Severity.ERROR, check='coverage_report_missing',
                    domain=ValidationDomain.DATA,
                    message=f'No coverage report available for '
                            f'{scenario.data_broker_type}/{scenario.symbol}',
                    scope=scenario.name)])
                scenario.validation_result.append(validation_result)
                self._logger.error(
                    f'❌ {scenario.name}: No coverage report for {scenario.data_broker_type}/{scenario.symbol}'
                )
                continue

            # === STEP 3: Validate data availability ===
            availability_errors = self._validator.validate_data_availability(
                scenario, report)

            if availability_errors:
                for error in availability_errors:
                    self._logger.error(f'❌ {scenario.name}: {error}')

                validation_result = ValidationResult(scenario.name, [
                    ValidationFinding(
                        severity=Severity.ERROR, check='data_availability',
                        domain=ValidationDomain.DATA,
                        message=error, scope=scenario.name)
                    for error in availability_errors])
                scenario.validation_result.append(validation_result)
                continue

            # === STEP 4: Validate signal availability (#429 sources only) ===
            signal_errors, signal_warnings = self._validator.validate_signal_availability(
                scenario)

            for error in signal_errors:
                self._logger.error(f'❌ {scenario.name}: {error}')
            for warning in signal_warnings:
                self._logger.warning(f'⚠️  {scenario.name}: {warning}')

            if signal_errors:
                validation_result = ValidationResult(scenario.name, [
                    ValidationFinding(
                        severity=Severity.ERROR, check='signal_availability',
                        domain=ValidationDomain.DATA,
                        message=error, scope=scenario.name)
                    for error in signal_errors] + [
                    ValidationFinding(
                        severity=Severity.WARNING, check='signal_availability',
                        domain=ValidationDomain.DATA,
                        message=warning, scope=scenario.name)
                    for warning in signal_warnings])
                scenario.validation_result.append(validation_result)
                continue

            # All checks passed
            validation_result = ValidationResult(scenario.name, [
                ValidationFinding(
                    severity=Severity.WARNING, check='signal_availability',
                    domain=ValidationDomain.DATA,
                    message=warning, scope=scenario.name)
                for warning in signal_warnings])
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
        self._logger.info('🔍 Phase 1.5: Validating data quality...')

        # Validate all scenarios
        self._validator.validate_loaded_data(
            scenarios=scenarios,
            scenario_packages=scenario_packages,
            requirements_map=requirements_map
        )
