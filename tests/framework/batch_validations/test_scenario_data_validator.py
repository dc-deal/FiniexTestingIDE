"""
FiniexTestingIDE - ScenarioDataValidator Unit Tests

Covers:
- validate_data_availability() — a coverage report with no date range is reported as a
  scenario error rather than crashing the batch, and the range checks still fire when
  a range is present
- the data-origin gate (#518) — which origins a scenario may READ, and the warning that
  measures what arming the gate would cost before it is armed
- the gap checks (#599) — a start inside a gap and the gaps inside a tick stretch, asked of the
  report by time window rather than by scanning every gap, with unchanged conclusions
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.types.coverage_report_types import Gap, GapCategory
from python.framework.validators.scenario_data_validator import ScenarioDataValidator


def _validator() -> ScenarioDataValidator:
    """
    Build a validator whose collaborators are irrelevant to availability checking.

    Returns:
        Validator instance
    """
    return ScenarioDataValidator(
        data_coverage_reports={},
        app_config=MagicMock(),
        logger=MagicMock(),
    )


def _scenario(start: datetime, end: datetime) -> MagicMock:
    """
    Build a scenario stub with the fields the availability check reads.

    Args:
        start: Scenario start date
        end: Scenario end date

    Returns:
        Scenario stub
    """
    scenario = MagicMock()
    scenario.start_date = start
    scenario.end_date = end
    scenario.symbol = 'BTCUSD'
    scenario.data_broker_type = 'kraken_spot'
    return scenario


def _report(start, end) -> MagicMock:
    """
    Build a coverage report stub.

    Args:
        start: Earliest available tick, or None for a report that found nothing
        end: Latest available tick, or None

    Returns:
        Report stub
    """
    report = MagicMock()
    report.start_time = start
    report.end_time = end
    return report


class TestMissingCoverageRange:
    """A report that found no data must produce an error, never an exception."""

    def test_absent_range_is_an_error_not_a_crash(self):
        # DataCoverageReport is constructed with start_time/end_time None and keeps them
        # when its analysis finds no files. Dereferencing that None used to raise an
        # AttributeError that aborted the whole batch (§33).
        errors = _validator().validate_data_availability(
            _scenario(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 2, 1, tzinfo=timezone.utc),
            ),
            _report(None, None),
        )
        assert len(errors) == 1

    def test_the_error_names_the_broker_and_symbol(self):
        # The operator has to know WHICH scenario to import data for.
        errors = _validator().validate_data_availability(
            _scenario(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 2, 1, tzinfo=timezone.utc),
            ),
            _report(None, None),
        )
        assert 'kraken_spot' in errors[0]
        assert 'BTCUSD' in errors[0]

    def test_a_half_filled_range_is_refused_too(self):
        errors = _validator().validate_data_availability(
            _scenario(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 2, 1, tzinfo=timezone.utc),
            ),
            _report(datetime(2026, 1, 1, tzinfo=timezone.utc), None),
        )
        assert len(errors) == 1


class TestRangeChecksStillFire:
    """The guard must not swallow the checks it stands in front of."""

    def test_scenario_inside_the_range_is_clean(self):
        errors = _validator().validate_data_availability(
            _scenario(
                datetime(2026, 1, 10, tzinfo=timezone.utc),
                datetime(2026, 1, 20, tzinfo=timezone.utc),
            ),
            _report(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 2, 1, tzinfo=timezone.utc),
            ),
        )
        assert errors == []

    def test_start_before_available_data_is_reported(self):
        errors = _validator().validate_data_availability(
            _scenario(
                datetime(2025, 12, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 20, tzinfo=timezone.utc),
            ),
            _report(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 2, 1, tzinfo=timezone.utc),
            ),
        )
        assert len(errors) == 1
        assert 'BEFORE' in errors[0]

    def test_end_after_available_data_is_reported(self):
        errors = _validator().validate_data_availability(
            _scenario(
                datetime(2026, 1, 10, tzinfo=timezone.utc),
                datetime(2026, 3, 1, tzinfo=timezone.utc),
            ),
            _report(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 2, 1, tzinfo=timezone.utc),
            ),
        )
        assert len(errors) == 1
        assert 'AFTER' in errors[0]


def _origin_validator(admitted: list) -> ScenarioDataValidator:
    """
    Build a validator with an explicit origin policy.

    Args:
        admitted: The origin classes a scenario may read

    Returns:
        Validator instance
    """
    app_config = MagicMock()
    app_config.get_admitted_origin_classes.return_value = admitted
    return ScenarioDataValidator(
        data_coverage_reports={},
        app_config=app_config,
        logger=MagicMock(),
    )


def _origin_scenario(classes: list, grades: list) -> MagicMock:
    """
    Build a scenario stub carrying what its loaded files resolved to.

    Args:
        classes: One origin class per overlapping archive file
        grades: One evidence grade per overlapping archive file

    Returns:
        Scenario stub
    """
    scenario = MagicMock()
    scenario.name = 'btcusd_60d'
    scenario.origin_classes = classes
    scenario.origin_evidence_grades = grades
    return scenario


class TestTheDataOriginGate:

    def test_an_inadmissible_class_excludes_the_scenario(self):
        errors = _origin_validator(['production'])._validate_data_origin(
            _origin_scenario(['production', 'development'], ['stamped', 'stamped']))
        assert len(errors) == 1
        assert '1/2' in errors[0]
        assert 'development' in errors[0]

    def test_an_admitted_class_passes(self):
        errors = _origin_validator(
            ['production', 'development'])._validate_data_origin(
            _origin_scenario(['production', 'development'], ['stamped', 'stamped']))
        assert errors == []

    def test_the_open_default_admits_everything(self):
        # The state this ships in, and the reason it does: every file resolves to `unknown`
        # until a producer stamps an identity, so a strict default would refuse every scenario
        # on day one — which is how a gate gets switched off rather than narrowed.
        errors = _origin_validator(
            ['production', 'development', 'unknown'])._validate_data_origin(
            _origin_scenario(['unknown', 'unknown'], ['unknown', 'unknown']))
        assert errors == []

    def test_a_scenario_that_loaded_nothing_is_not_judged(self):
        assert _origin_validator(['production'])._validate_data_origin(
            _origin_scenario([], [])) == []


GAP_T = datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc)


def _gap_at(start: datetime, minutes: float, category: GapCategory) -> Gap:
    """
    A gap of a category and a length.

    Args:
        start: Gap start
        minutes: Length in minutes
        category: Its category

    Returns:
        The gap
    """
    return Gap(gap_seconds=minutes * 60, category=category, reason='test',
               gap_start=start, gap_end=start + timedelta(minutes=minutes))


def _gap_validator() -> ScenarioDataValidator:
    """A validator that allows weekend, holiday and short gaps inside a tick stretch."""
    app_config = MagicMock()
    app_config.get_allowed_gap_categories.return_value = ['weekend', 'holiday', 'short']
    app_config.get_admitted_origin_classes.return_value = []
    return ScenarioDataValidator(
        data_coverage_reports={}, app_config=app_config, logger=MagicMock())


def _gap_report(*gaps: Gap) -> DataCoverageReport:
    """A real coverage report holding the given gaps — its window queries are the real ones."""
    report = DataCoverageReport(symbol='BTCUSD', broker_type='kraken_spot')
    report.gaps = list(gaps)
    return report


class TestTheGapChecksSearchTheReport:
    """
    A symbol with years of a venue's history holds hundreds of thousands of gaps, and these two
    checks run once per scenario: they ask the report by time window now instead of scanning
    every gap (#599). What they conclude must not change.
    """

    def test_a_start_inside_a_short_gap_moves_to_its_end(self):
        gap = _gap_at(GAP_T, 10, GapCategory.SHORT)
        scenario = SimpleNamespace(start_date=GAP_T + timedelta(minutes=4))

        errors, warnings = _gap_validator()._validate_start_date_not_in_gap(
            scenario, _gap_report(gap))

        assert errors == []
        assert scenario.start_date == gap.gap_end
        assert warnings and warnings[0].startswith('start_date shifted')

    def test_a_start_inside_a_large_gap_is_an_error(self):
        scenario = SimpleNamespace(start_date=GAP_T + timedelta(hours=1))

        errors, warnings = _gap_validator()._validate_start_date_not_in_gap(
            scenario, _gap_report(_gap_at(GAP_T, 300, GapCategory.LARGE)))

        assert warnings == [] and 'is inside large gap' in errors[0]

    def test_a_start_on_a_gap_s_edge_is_not_inside_it(self):
        scenario = SimpleNamespace(start_date=GAP_T)

        errors, warnings = _gap_validator()._validate_start_date_not_in_gap(
            scenario, _gap_report(_gap_at(GAP_T, 300, GapCategory.LARGE)))

        assert (errors, warnings) == ([], [])
        assert scenario.start_date == GAP_T

    def test_a_stretch_reports_only_the_forbidden_gaps_wholly_inside_it(self):
        first, last = GAP_T, GAP_T + timedelta(days=1)
        inside_large = _gap_at(GAP_T + timedelta(hours=2), 300, GapCategory.LARGE)
        inside_short = _gap_at(GAP_T + timedelta(hours=9), 10, GapCategory.SHORT)
        across_the_end = _gap_at(last - timedelta(hours=1), 300, GapCategory.LARGE)
        scenario = SimpleNamespace(name='scenario_1')
        package = SimpleNamespace(ticks={'scenario_1': object()})

        with patch('python.framework.validators.scenario_data_validator.'
                   'time_range_from_transport_ticks', return_value=(first, last)):
            errors = _gap_validator()._validate_tick_stretch(
                scenario, _gap_report(inside_large, inside_short, across_the_end), package)

        assert len(errors) == 1
        assert inside_large.gap_start.strftime('%Y-%m-%d %H:%M:%S') in errors[0]
