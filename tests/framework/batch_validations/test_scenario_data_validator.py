"""
FiniexTestingIDE - ScenarioDataValidator Unit Tests

Covers:
- validate_data_availability() — a coverage report with no date range is reported as a
  scenario error rather than crashing the batch, and the range checks still fire when
  a range is present
- the data-origin gate (#518) — which origins a scenario may READ, and the warning that
  measures what arming the gate would cost before it is armed
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

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
