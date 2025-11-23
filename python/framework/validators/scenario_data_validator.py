"""
FiniexTestingIDE - Scenario Data Validator
Validates scenario configurations against data availability and quality requirements

Phase 1.5: Post-Load Data Quality Validation
Location: python/framework/validators/scenario_data_validator.py
"""

from datetime import datetime, timezone
from typing import Dict, List, Tuple
from dateutil import parser

from python.components.logger.abstract_logger import AbstractLogger
from python.configuration import AppConfigManager
from python.framework.types.validation_types import ValidationResult
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.process_data_types import ProcessDataPackage, RequirementsMap
from python.framework.reporting.coverage_report import CoverageReport
from python.framework.types.coverage_report_types import GapCategory


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
            coverage_reports: Dict mapping symbol to CoverageReport
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

    def _ensure_utc_aware(self, dt: datetime) -> datetime:
        """
        Ensure datetime is UTC-aware.

        Project policy: All datetimes must be UTC-aware.

        Args:
            dt: Datetime object (naive or aware)

        Returns:
            UTC-aware datetime
        """
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def validate_loaded_data(
        self,
        scenarios: List[SingleScenario],
        shared_data: ProcessDataPackage,
        requirements_map: RequirementsMap
    ) -> Tuple[List[Tuple[SingleScenario, ValidationResult]], List[Tuple[SingleScenario, ValidationResult]]]:
        """
        Validate scenarios after data has been loaded.

        Checks:
        1. start_date not in gap
        2. Tick stretch (first_tick → last_tick) free of forbidden gaps
        3. Warmup bars quality (no synthetic in standard mode)

        Args:
            scenarios: List of scenarios to validate
            shared_data: Loaded tick and bar data
            requirements_map: Requirements map for warmup info

        Returns:
            Tuple of (valid_scenarios, invalid_scenarios_with_results)
        """
        valid_scenarios: List[Tuple[SingleScenario, ValidationResult]] = []
        invalid_scenarios: List[Tuple[SingleScenario, ValidationResult]] = []

        for scenario in scenarios:
            result = self._validate_single_scenario(
                scenario, shared_data, requirements_map
            )

            if result.is_valid:
                # Log warnings if any
                for warning in result.warnings:
                    self._logger.warning(f"⚠️  {scenario.name}: {warning}")
                valid_scenarios.append((scenario, result))
            else:
                # Log errors
                for error in result.errors:
                    self._logger.error(f"❌ {scenario.name}: {error}")
                invalid_scenarios.append((scenario, result))

        # Summary
        if invalid_scenarios:
            self._logger.warning(
                f"⚠️  {len(invalid_scenarios)}/{len(scenarios)} scenarios failed validation"
            )

        return valid_scenarios, invalid_scenarios

    def _validate_single_scenario(
        self,
        scenario: SingleScenario,
        shared_data: ProcessDataPackage,
        requirements_map: RequirementsMap
    ) -> ValidationResult:
        """
        Validate a single scenario.

        Args:
            scenario: Scenario to validate
            shared_data: Loaded data package
            requirements_map: Requirements map

        Returns:
            ValidationResult with errors and warnings
        """
        errors = []
        warnings = []

        # Get coverage report for this symbol
        report = self._coverage_reports.get(scenario.symbol)
        if not report:
            errors.append(
                f"No coverage report available for {scenario.symbol}")
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
            scenario, report, shared_data)
        errors.extend(stretch_errors)

        # === VALIDATION 3: Warmup quality ===
        warmup_errors, warmup_warnings = self._validate_warmup_quality(
            scenario, shared_data, requirements_map
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
        start_date = self._parse_datetime(scenario.start_date)

        for gap in report.gaps:
            # Ensure all datetimes are UTC-aware for comparison
            gap_start = self._ensure_utc_aware(gap.file1.end_time)
            gap_end = self._ensure_utc_aware(gap.file2.start_time)

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
        shared_data: ProcessDataPackage
    ) -> List[str]:
        """
        Validate that tick stretch is free of forbidden gaps.

        Args:
            scenario: Scenario to validate
            report: Coverage report for symbol
            shared_data: Loaded data package

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Get actual loaded tick range
        tick_data = shared_data.ticks.get(scenario.name)
        if not tick_data:
            # No ticks loaded - cannot validate stretch
            return errors

        first_tick = self._ensure_utc_aware(tick_data[0]['timestamp'])
        last_tick = self._ensure_utc_aware(tick_data[-1]['timestamp'])

        # Check gaps in this stretch
        for gap in report.gaps:
            # Ensure gap timestamps are UTC-aware
            gap_start = self._ensure_utc_aware(gap.file1.end_time)
            gap_end = self._ensure_utc_aware(gap.file2.start_time)

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
        shared_data: ProcessDataPackage,
        requirements_map: RequirementsMap
    ) -> Tuple[List[str], List[str]]:
        """
        Validate warmup bar quality based on warmup_quality_mode.

        Args:
            scenario: Scenario to validate
            shared_data: Loaded data package
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
            bar_data = shared_data.bars.get(bar_key)

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

    def _parse_datetime(self, dt_str: str) -> datetime:
        """
        Parse datetime string to UTC-aware datetime object.

        Args:
            dt_str: Datetime string (ISO format)

        Returns:
            UTC-aware datetime object
        """
        dt = parser.parse(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
