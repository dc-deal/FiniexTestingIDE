"""
FiniexTestingIDE - Scenario Data Validator
Validates scenario configurations against data availability and quality requirements

Phase 1.5: Post-Load Data Quality Validation
Location: python/framework/validators/scenario_data_validator.py
"""

from typing import Dict, List, Tuple

from python.framework.logging.abstract_logger import AbstractLogger
from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.validation_types import ValidationResult
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.process_data_types import ProcessDataPackage, RequirementsMap
from python.framework.reporting.coverage_report import CoverageReport
from python.framework.types.coverage_report_types import GapCategory
from python.framework.utils.time_utils import ensure_utc_aware


class ScenarioDataValidator:
    """
    Validates scenarios against data quality requirements.

    Two-stage validation:
    1. Pre-load: Basic checks (start_date not in gap)
    2. Post-load: Quality checks (tick stretch, warmup quality)

    Configuration via app_config:
    - warmup_quality_mode: 'permissive' or 'standard'
    - allowed_gap_categories: List of allowed gap types
    """

    def __init__(
        self,
        coverage_reports: Dict[str, CoverageReport],
        app_config: AppConfigManager,
        logger: AbstractLogger
    ):
        """
        Initialize validator.

        Args:
            coverage_reports: Dict mapping (broker_type, symbol) tuple to CoverageReport
            app_config: Application config manager
            logger: Logger instance
        """

        self._coverage_reports = coverage_reports
        self._app_config = app_config
        self._logger = logger

        # Load validation settings from config
        self._warmup_quality_mode = app_config.get_warmup_quality_mode()
        self._allowed_gap_categories = self._load_allowed_gap_categories()

    def _load_allowed_gap_categories(self) -> List[GapCategory]:
        """
        Load allowed gap categories from config.

        Returns:
            List of allowed GapCategory enums
        """
        category_strings = self._app_config.get_allowed_gap_categories()

        # Convert strings to GapCategory enums
        category_map = {
            'seamless': GapCategory.SEAMLESS,
            'weekend': GapCategory.WEEKEND,
            'holiday': GapCategory.HOLIDAY,
            'short': GapCategory.SHORT,
            'moderate': GapCategory.MODERATE,
            'large': GapCategory.LARGE
        }

        allowed_categories = []
        for cat_str in category_strings:
            cat_enum = category_map.get(cat_str.lower())
            if cat_enum:
                allowed_categories.append(cat_enum)
            else:
                self._logger.warning(
                    f"⚠️  Unknown gap category '{cat_str}' in config - ignored"
                )

        return allowed_categories

    def validate_date_logic(
        self,
        scenario: SingleScenario
    ) -> List[str]:
        """
        Validate basic date logic (config sanity check).

        Checks:
        - end_date must be after start_date

        Args:
            scenario: Scenario to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        start_date = scenario.start_date
        end_date = scenario.end_date if scenario.end_date else None

        # Check basic logic
        if end_date and end_date < start_date:
            errors.append(
                f"Invalid date range: end_date {end_date.strftime('%Y-%m-%d %H:%M:%S')} UTC "
                f"is BEFORE start_date {start_date.strftime('%Y-%m-%d %H:%M:%S')} UTC. "
                f"This is a configuration error."
            )

        return errors

    def validate_data_availability(
        self,
        scenario: SingleScenario,
        report: CoverageReport
    ) -> List[str]:
        """
        Validate that scenario dates are within available data range.

        Assumes date logic is already validated (_validate_date_logic).

        Checks:
        - start_date must be >= first available tick
        - end_date must be <= last available tick

        Args:
            scenario: Scenario to validate
            report: Coverage report for symbol

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        start_date = scenario.start_date
        end_date = scenario.end_date if scenario.end_date else None

        # Get data range from coverage report
        data_start = ensure_utc_aware(report.start_time)
        data_end = ensure_utc_aware(report.end_time)

        # Check if start_date is before available data
        if start_date < data_start:
            errors.append(
                f"start_date {start_date.strftime('%Y-%m-%d %H:%M:%S')} UTC is BEFORE "
                f"available data range (earliest: {data_start.strftime('%Y-%m-%d %H:%M:%S')} UTC). "
                f"No ticks exist for this period! Adjust start_date to >= {data_start.strftime('%Y-%m-%d')}."
            )

         # Check if end_date is after available data
        if end_date and end_date > data_end:
            errors.append(
                f"end_date {end_date.strftime('%Y-%m-%d %H:%M:%S')} UTC is AFTER "
                f"available data range (latest: {data_end.strftime('%Y-%m-%d %H:%M:%S')} UTC). "
                f"Adjust end_date to <= {data_end.strftime('%Y-%m-%d')}."
            )

        return errors

    def validate_loaded_data(
        self,
        scenarios: List[SingleScenario],
        scenario_packages: Dict[int, ProcessDataPackage],
        requirements_map: RequirementsMap
    ):
        """
        Validate scenarios after data has been loaded.

        Checks:
        1. start_date not in gap
        2. Tick stretch (first_tick → last_tick) free of forbidden gaps
        3. Warmup bars quality (no synthetic in standard mode)

        Args:
            scenarios: List of scenarios to validate
            scenario_packages: Dict mapping scenario index to its ProcessDataPackage
            requirements_map: Requirements map for warmup info

        Returns:
            Tuple of (valid_scenarios, invalid_scenarios_with_results)
        """

        for idx, scenario in enumerate(scenarios):
            # Get scenario-specific data package
            scenario_package = scenario_packages.get(idx)
            if not scenario_package:
                # Missing package - create error result
                result = ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=[
                        f"No data package found for scenario index {idx}"],
                    warnings=[]
                )
                continue

            result = self._validate_single_scenario(
                scenario, scenario_package, requirements_map
            )

            if result.is_valid:
                # Log warnings if any
                for warning in result.warnings:
                    self._logger.warning(f"⚠️  {scenario.name}: {warning}")
            else:
                # Log errors
                for error in result.errors:
                    self._logger.error(f"❌ {scenario.name}: {error}")
                    scenario.validation_result.append(result)

    def _validate_single_scenario(
        self,
        scenario: SingleScenario,
        scenario_package: ProcessDataPackage,
        requirements_map: RequirementsMap
    ) -> ValidationResult:
        """
        Validate a single scenario.

        Args:
            scenario: Scenario to validate
            scenario_package: Data package for this specific scenario
            requirements_map: Requirements map

        Returns:
            ValidationResult with errors and warnings
        """
        errors = []
        warnings = []

        # Get coverage report for this symbol
        report_key = (scenario.data_broker_type, scenario.symbol)
        report = self._coverage_reports.get(report_key)
        if not report:
            errors.append(
                f"No coverage report available for {scenario.data_broker_type}/{scenario.symbol}")
            return ValidationResult(
                is_valid=False,
                scenario_name=scenario.name,
                errors=errors,
                warnings=warnings
            )

        # === VALIDATION 1: start_date not in gap ===
        start_date_errors = self._validate_start_date_not_in_gap(
            scenario, report)
        errors.extend(start_date_errors)

        # === VALIDATION 2: Tick stretch gaps ===
        stretch_errors = self._validate_tick_stretch(
            scenario, report, scenario_package)
        errors.extend(stretch_errors)

        # === VALIDATION 3: Warmup quality ===
        warmup_errors, warmup_warnings = self._validate_warmup_quality(
            scenario, scenario_package, requirements_map
        )
        errors.extend(warmup_errors)
        warnings.extend(warmup_warnings)

        return ValidationResult(
            is_valid=len(errors) == 0,
            scenario_name=scenario.name,
            errors=errors,
            warnings=warnings
        )

    def _validate_start_date_not_in_gap(
        self,
        scenario: SingleScenario,
        report: CoverageReport
    ) -> List[str]:
        """
        Validate that start_date is not inside a gap.

        Args:
            scenario: Scenario to validate
            report: Coverage report for symbol

        Returns:
            List of error messages (empty if valid)
        """
        errors = []
        start_date = scenario.start_date

        for gap in report.gaps:
            # Ensure all datetimes are UTC-aware for comparison
            gap_start = ensure_utc_aware(gap.gap_start)
            gap_end = ensure_utc_aware(gap.gap_end)

            if gap_start < start_date < gap_end:
                errors.append(
                    f"start_date {start_date.strftime('%Y-%m-%d %H:%M:%S')} UTC is inside "
                    f"{gap.category.value} gap ({gap_start.strftime('%Y-%m-%d %H:%M:%S')} → "
                    f"{gap_end.strftime('%Y-%m-%d %H:%M:%S')}). "
                    f"No ticks available! Next valid start: {gap_end.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )
                break

        return errors

    def _validate_tick_stretch(
        self,
        scenario: SingleScenario,
        report: CoverageReport,
        scenario_package: ProcessDataPackage
    ) -> List[str]:
        """
        Validate that tick stretch is free of forbidden gaps.

        Args:
            scenario: Scenario to validate
            report: Coverage report for symbol
            scenario_package: Data package for this specific scenario

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Get actual loaded tick range
        tick_data = scenario_package.ticks.get(scenario.name)
        if not tick_data:
            # No ticks loaded - cannot validate stretch
            return errors

        first_tick = ensure_utc_aware(tick_data[0]['timestamp'])
        last_tick = ensure_utc_aware(tick_data[-1]['timestamp'])

        # Check gaps in this stretch
        for gap in report.gaps:
            # Ensure gap timestamps are UTC-aware
            gap_start = ensure_utc_aware(gap.gap_start)
            gap_end = ensure_utc_aware(gap.gap_end)

            # Gap overlaps with tick stretch?
            if (gap_start >= first_tick and gap_end <= last_tick):

                if gap.category not in self._allowed_gap_categories:
                    errors.append(
                        f"{gap.severity_icon} {gap.category.value.upper()} gap detected in tick stretch "
                        f"({first_tick.strftime('%Y-%m-%d %H:%M:%S')} → {last_tick.strftime('%Y-%m-%d %H:%M:%S')}): "
                        f"{gap_start.strftime('%Y-%m-%d %H:%M:%S')} → "
                        f"{gap_end.strftime('%Y-%m-%d %H:%M:%S')} ({gap.duration_human}). "
                        f"Not allowed in '{self._warmup_quality_mode}' mode"
                    )

        return errors

    def _validate_warmup_quality(
        self,
        scenario: SingleScenario,
        scenario_package: ProcessDataPackage,
        requirements_map: RequirementsMap
    ) -> Tuple[List[str], List[str]]:
        """
        Validate warmup bar quality based on warmup_quality_mode.

        Args:
            scenario: Scenario to validate
            scenario_package: Data package for this specific scenario
            requirements_map: Requirements map with bar requirements

        Returns:
            Tuple of (errors, warnings)
        """
        errors = []
        warnings = []

        # Only validate in standard mode (permissive just warns)
        if self._warmup_quality_mode == "permissive":
            return errors, warnings

        # Get bar requirements for this scenario
        for bar_req in requirements_map.bar_requirements:
            if bar_req.scenario_name != scenario.name:
                continue

            # Get bar data
            bar_key = (bar_req.symbol, bar_req.timeframe, bar_req.start_time)
            bar_data = scenario_package.bars.get(bar_key)

            if not bar_data:
                continue

            # Check for synthetic bars
            synthetic_count = sum(
                1 for bar in bar_data
                if bar.get('bar_type') == 'synthetic'
            )

            if synthetic_count > 0:
                total_bars = len(bar_data)
                synthetic_pct = (synthetic_count / total_bars) * 100

                errors.append(
                    f"Warmup for {bar_req.timeframe} contains {synthetic_count}/{total_bars} "
                    f"synthetic bars ({synthetic_pct:.1f}%) - not allowed in standard mode. "
                    f"Adjust start_date to avoid gaps in warmup period."
                )

        return errors, warnings
