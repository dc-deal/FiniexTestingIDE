"""
FiniexTestingIDE - Scenario Data Validator
Validates scenario configurations against data availability and quality requirements

Phase 1.5: Post-Load Data Quality Validation
"""

from typing import Dict, List, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.discoveries.signal_coverage.signal_coverage_report import SignalCoverageReport
from python.framework.exceptions.market_compatibility_errors import MarketCompatibilityError
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.coverage_report_types import GapCategory
from python.framework.types.process_data_types import ProcessDataPackage, RequirementsMap
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
    ValidationResult,
)
from python.framework.utils.process_serialization_utils import time_range_from_transport_ticks
from python.framework.utils.time_utils import ensure_utc_aware, format_duration


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
        data_coverage_reports: Dict[str, DataCoverageReport],
        app_config: AppConfigManager,
        logger: AbstractLogger,
        signal_coverage_reports: Dict[Tuple[str, str], SignalCoverageReport] = None
    ):
        """
        Initialize validator.

        Args:
            data_coverage_reports: Dict mapping (broker_type, symbol) tuple to DataCoverageReport
            app_config: Application config manager
            logger: Logger instance
            signal_coverage_reports: Dict mapping (data_sentiment_type, symbol) tuple to
                SignalCoverageReport — empty when no scenario binds a signal source
        """

        self._data_coverage_reports = data_coverage_reports
        self._signal_coverage_reports = signal_coverage_reports or {}
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
        report: DataCoverageReport
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

    def validate_signal_availability(
        self,
        scenario: SingleScenario
    ) -> Tuple[List[str], List[str]]:
        """
        Validate the scenario's signal source against its coverage (pre-load).

        Five checks. One is about the data's nature, four about the window — the
        point where a SIGNAL worker either has something to resolve or does not:
        - the source declares itself synthetic (generated, not a market record)
        - the source/symbol carries no snapshots at all (config/data error)
        - the window closes before the series opens: nothing can ever resolve
        - no snapshot at or before start_date: the run begins blind, every tick
          until the first snapshot resolves to a gap (empty result, is_stale)
        - start_date sits inside a gap: the run begins on an already-aged snapshot

        The tail is deliberately NOT checked. A window reaching past the last
        snapshot is a legitimate, contracted degradation (#434) — the signal goes
        stale and the decision logic reacts.

        Args:
            scenario: Scenario to validate

        Returns:
            Tuple of (error messages, warning messages)
        """
        errors = []
        warnings = []

        # Early exit: scenario does not bind a signal source
        if not scenario.data_sentiment_type:
            return errors, warnings

        report_key = (scenario.data_sentiment_type, scenario.symbol)
        report = self._signal_coverage_reports.get(report_key)
        if not report or not report.snapshot_count:
            errors.append(
                f"No signal data for source '{scenario.data_sentiment_type}' / "
                f"symbol {scenario.symbol}. The source is not imported, or carries "
                f"no snapshot for this symbol."
            )
            return errors, warnings

        # === Synthetic data: a result on generated signals is not a market result ===
        if report.is_synthetic():
            warnings.append(
                f"Signal '{scenario.data_sentiment_type}' is SYNTHETIC "
                f"(data_origin=synthetic) — generated data, not a market record. "
                f"Valid for pipeline validation, meaningless as a performance statement."
            )

        start_date = scenario.start_date
        end_date = scenario.end_date

        # === No overlap at all: the window closes before the series opens ===
        if end_date and end_date < report.start_time:
            errors.append(
                f"Signal '{scenario.data_sentiment_type}': the scenario window "
                f"({start_date.strftime('%Y-%m-%d %H:%M')} → "
                f"{end_date.strftime('%Y-%m-%d %H:%M')} UTC) closes before the source "
                f"begins ({report.start_time.strftime('%Y-%m-%d %H:%M')} UTC). "
                f"No snapshot can ever resolve."
            )
            return errors, warnings

        # === Blind head: nothing to resolve at the first tick ===
        if not report.has_snapshot_at_or_before(start_date):
            blind_s = (report.start_time - start_date).total_seconds()
            warnings.append(
                f"Signal '{scenario.data_sentiment_type}': no snapshot at or before "
                f"start_date {start_date.strftime('%Y-%m-%d %H:%M:%S')} UTC — the first "
                f"{format_duration(blind_s)} resolve BLIND (empty signal, is_stale) — "
                f"counted as blind ticks in the run's signal report. "
                f"First snapshot: {report.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC"
            )
            return errors, warnings

        # === Aged head: start_date sits inside a gap ===
        resolved = report.latest_snapshot_at_or_before(start_date)
        age_s = (start_date - resolved).total_seconds()
        if age_s > report.cadence_seconds * 2:
            warnings.append(
                f"Signal '{scenario.data_sentiment_type}': the run starts on a snapshot "
                f"already {format_duration(age_s)} old "
                f"({resolved.strftime('%Y-%m-%d %H:%M:%S')} UTC, cadence "
                f"{format_duration(report.cadence_seconds)})"
            )

        return errors, warnings

    @staticmethod
    def validate_worker_market_compatibility(
        scenario: SingleScenario,
        worker_factory: WorkerFactory,
        market_config_manager: MarketConfigManager,
    ) -> List[str]:
        """
        Validate that all workers in this scenario are compatible with the
        broker's market activity metric.

        Resolves each worker class via the factory (static, no instance),
        calls the mandatory get_required_activity_metric() classmethod, and
        cross-references it against the broker's primary_activity_metric
        from market_config.json.

        A missing classmethod override raises NotImplementedError — this is
        caught and reported as a configuration error so the scenario fails
        pre-flight instead of during subprocess execution.

        Staticmethod — uses no instance state. Called from RequirementsCollector
        (Phase 3) as the first per-scenario check before data requirements are
        aggregated.

        Args:
            scenario: Scenario to validate
            worker_factory: Worker factory used to resolve worker classes
            market_config_manager: Market config lookup for activity metric

        Returns:
            List of error messages (empty if valid)
        """
        errors: List[str] = []

        strategy_config = scenario.strategy_config or {}
        worker_instances = strategy_config.get('worker_instances', {})

        if not worker_instances:
            return errors

        # Broker metric (single lookup per scenario)
        try:
            broker_metric = market_config_manager.get_primary_activity_metric_for_broker(
                scenario.data_broker_type
            )
            market_type = market_config_manager.get_market_type(
                scenario.data_broker_type
            ).value
        except ValueError as e:
            errors.append(
                f"Cannot resolve market metric for broker "
                f"'{scenario.data_broker_type}': {e}"
            )
            return errors

        for instance_name, worker_type in worker_instances.items():
            try:
                worker_class, _ = worker_factory.resolve_worker_class(worker_type)
            except ValueError as e:
                errors.append(
                    f"Cannot resolve worker '{instance_name}' ({worker_type}): {e}"
                )
                continue

            try:
                required_metric = worker_class.get_required_activity_metric()
            except NotImplementedError as e:
                errors.append(
                    f"Worker '{instance_name}' ({worker_type}) does not declare "
                    f"get_required_activity_metric(). {e}"
                )
                continue

            if required_metric is None:
                # Price-based worker — no activity-data dependency
                continue

            if required_metric != broker_metric:
                compat_error = MarketCompatibilityError(
                    scenario_name=scenario.name,
                    worker_instance_name=instance_name,
                    worker_type=worker_type,
                    required_metric=required_metric,
                    broker_type=scenario.data_broker_type,
                    broker_metric=broker_metric,
                    market_type=market_type,
                )
                errors.append(str(compat_error))

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
                result = ValidationResult(scenario.name, [ValidationFinding(
                    severity=Severity.ERROR, check='data_package_missing',
                    domain=ValidationDomain.DATA,
                    message=f'No data package found for scenario index {idx}',
                    scope=scenario.name)])
                continue

            result = self._validate_single_scenario(
                scenario, scenario_package, requirements_map
            )

            if result.is_valid:
                # Log warnings if any
                for warning in result.warnings:
                    self._logger.warning(f'⚠️  {scenario.name}: {warning}')
                if result.warnings:
                    scenario.validation_result.append(result)
            else:
                # Log errors
                for error in result.errors:
                    self._logger.error(f'❌ {scenario.name}: {error}')
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
        findings: List[ValidationFinding] = []

        # Get coverage report for this symbol
        report_key = (scenario.data_broker_type, scenario.symbol)
        report = self._data_coverage_reports.get(report_key)
        if not report:
            return ValidationResult(scenario.name, [ValidationFinding(
                severity=Severity.ERROR, check='coverage_report_missing',
                domain=ValidationDomain.DATA,
                message=f'No coverage report available for '
                        f'{scenario.data_broker_type}/{scenario.symbol}',
                scope=scenario.name)])

        # Each sub-validator returns plain message lists; they are attributed to their own
        # check HERE, so a finding still names its origin after they are merged.
        def _as(messages: List[str], severity: Severity, check: str) -> List[ValidationFinding]:
            return [ValidationFinding(
                severity=severity, check=check, domain=ValidationDomain.DATA,
                message=message, scope=scenario.name) for message in messages]

        # === VALIDATION 1: start_date not in gap ===
        start_date_errors, start_date_warnings = self._validate_start_date_not_in_gap(
            scenario, report)
        findings.extend(_as(start_date_errors, Severity.ERROR, 'start_date_in_gap'))
        findings.extend(_as(start_date_warnings, Severity.WARNING, 'start_date_in_gap'))

        # === VALIDATION 2: Tick stretch gaps ===
        stretch_errors = self._validate_tick_stretch(
            scenario, report, scenario_package)
        findings.extend(_as(stretch_errors, Severity.ERROR, 'tick_stretch_gap'))

        # === VALIDATION 3: Warmup quality ===
        warmup_errors, warmup_warnings = self._validate_warmup_quality(
            scenario, scenario_package, requirements_map
        )
        findings.extend(_as(warmup_errors, Severity.ERROR, 'warmup_quality'))
        findings.extend(_as(warmup_warnings, Severity.WARNING, 'warmup_quality'))

        # === VALIDATION 4: Signal gaps in the tick stretch ===
        signal_errors = self._validate_signal_stretch(scenario, scenario_package)
        findings.extend(_as(signal_errors, Severity.ERROR, 'signal_stretch_gap'))

        return ValidationResult(scenario.name, findings)

    def _validate_start_date_not_in_gap(
        self,
        scenario: SingleScenario,
        report: DataCoverageReport
    ) -> Tuple[List[str], List[str]]:
        """
        Validate that start_date is not inside a gap.

        For short gaps, auto-corrects start_date to gap_end and emits
        a warning instead of an error. Moderate/large gaps remain errors.

        Args:
            scenario: Scenario to validate
            report: Coverage report for symbol

        Returns:
            Tuple of (error messages, warning messages)
        """
        errors = []
        warnings = []
        start_date = scenario.start_date

        for gap in report.gaps:
            # Ensure all datetimes are UTC-aware for comparison
            gap_start = ensure_utc_aware(gap.gap_start)
            gap_end = ensure_utc_aware(gap.gap_end)

            if gap_start < start_date < gap_end:
                if gap.category == GapCategory.SHORT:
                    # Auto-correct: shift start_date past the short gap
                    scenario.start_date = gap_end
                    gap_minutes = (gap_end - start_date).total_seconds() / 60
                    warnings.append(
                        f"start_date shifted {start_date.strftime('%Y-%m-%d %H:%M:%S')} → "
                        f"{gap_end.strftime('%Y-%m-%d %H:%M:%S')} UTC "
                        f"(inside short gap, +{gap_minutes:.0f}min)"
                    )
                else:
                    errors.append(
                        f"start_date {start_date.strftime('%Y-%m-%d %H:%M:%S')} UTC is inside "
                        f"{gap.category.value} gap ({gap_start.strftime('%Y-%m-%d %H:%M:%S')} → "
                        f"{gap_end.strftime('%Y-%m-%d %H:%M:%S')}). "
                        f"No ticks available! Next valid start: {gap_end.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                    )
                break

        return errors, warnings

    def _validate_tick_stretch(
        self,
        scenario: SingleScenario,
        report: DataCoverageReport,
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

        first_tick, last_tick = time_range_from_transport_ticks(tick_data)

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

    def _validate_signal_stretch(
        self,
        scenario: SingleScenario,
        scenario_package: ProcessDataPackage
    ) -> List[str]:
        """
        Validate that the signal series is free of forbidden gaps where ticks flow.

        Mirrors _validate_tick_stretch and shares its allowed_gap_categories
        config — only gaps inside the loaded tick stretch matter, because a
        signal is resolved at ticks and nowhere else.

        Args:
            scenario: Scenario to validate
            scenario_package: Data package for this specific scenario

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Early exit: scenario does not bind a signal source
        if not scenario.data_sentiment_type:
            return errors

        report = self._signal_coverage_reports.get(
            (scenario.data_sentiment_type, scenario.symbol))
        if not report:
            return errors

        tick_data = scenario_package.ticks.get(scenario.name)
        if not tick_data:
            # No ticks loaded - cannot validate stretch
            return errors

        first_tick, last_tick = time_range_from_transport_ticks(tick_data)

        for gap in report.gaps_in_window(first_tick, last_tick):
            if gap.category in self._allowed_gap_categories:
                continue

            coverage_pct = report.coverage_ratio_in_window(
                first_tick, last_tick) * 100
            errors.append(
                f"{gap.severity_icon} {gap.category.value.upper()} signal gap detected in tick "
                f"stretch ({first_tick.strftime('%Y-%m-%d %H:%M:%S')} → "
                f"{last_tick.strftime('%Y-%m-%d %H:%M:%S')}): "
                f"{gap.gap_start.strftime('%Y-%m-%d %H:%M:%S')} → "
                f"{gap.gap_end.strftime('%Y-%m-%d %H:%M:%S')} ({gap.duration_human}) "
                f"in source '{scenario.data_sentiment_type}'. "
                f"Signal coverage in stretch: {coverage_pct:.0f}%. "
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
        Validate warmup bar quality: synthetic bar check and bar count sufficiency.

        Args:
            scenario: Scenario to validate
            scenario_package: Data package for this specific scenario
            requirements_map: Requirements map with bar requirements

        Returns:
            Tuple of (errors, warnings)
        """
        errors = []
        warnings = []
        is_standard = self._warmup_quality_mode == 'standard'

        # Get bar requirements for this scenario
        for bar_req in requirements_map.bar_requirements:
            if bar_req.scenario_name != scenario.name:
                continue

            # Get bar data
            bar_key = (bar_req.symbol, bar_req.timeframe, bar_req.start_time)
            bar_data = scenario_package.bars.get(bar_key)

            if not bar_data:
                continue

            # Check bar count sufficiency
            actual_count = len(bar_data)
            if actual_count < bar_req.warmup_count:
                pct = (actual_count / bar_req.warmup_count * 100) if bar_req.warmup_count > 0 else 0
                msg = (
                    f'Warmup for {bar_req.timeframe} has {actual_count}/{bar_req.warmup_count} bars '
                    f'({pct:.0f}%) — insufficient for indicator stabilization.'
                )
                if is_standard:
                    errors.append(msg)
                else:
                    warnings.append(msg)

        return errors, warnings
