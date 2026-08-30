"""
Validation Finding Tests.

`ValidationFinding` is the atomic unit every validator produces: it carries its own severity,
the check that decided it, and the area it belongs to. `ValidationResult` is a typed collection
of them, and `is_valid` / `errors` / `warnings` are VIEWS over that collection rather than
stored state — a stored flag can disagree with the list it summarizes, a derived one cannot.

The §33 execution gate (`SingleScenario.is_valid()`) reads `ValidationResult.is_valid`, so the
derivation below is what decides whether a scenario runs.
"""

from datetime import datetime, timezone

from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
    ValidationResult,
)


def _finding(severity: Severity, message: str, check: str = 'c') -> ValidationFinding:
    return ValidationFinding(
        severity=severity, check=check, domain=ValidationDomain.DATA, message=message, scope='s1')


class TestSeverityDrivesValidity:
    def test_no_findings_is_valid(self):
        assert ValidationResult('s1').is_valid is True

    def test_an_advisory_alone_does_not_reject(self):
        result = ValidationResult('s1', [_finding(Severity.WARNING, 'slow feed')])
        assert result.is_valid is True
        assert result.has_warnings() and not result.has_errors()

    def test_one_error_rejects(self):
        result = ValidationResult('s1', [_finding(Severity.ERROR, 'no data')])
        assert result.is_valid is False
        assert result.has_errors()


class TestTheViewsCannotDisagreeWithTheFindings:
    def test_a_mixed_result_reports_both(self):
        """One container carries both — severity belongs to the finding, not the container."""
        result = ValidationResult('s1', [
            _finding(Severity.ERROR, 'signal source missing'),
            _finding(Severity.WARNING, 'signal coverage partial'),
        ])
        assert result.errors == ['signal source missing']
        assert result.warnings == ['signal coverage partial']
        assert result.is_valid is False

    def test_views_follow_a_finding_added_later(self):
        """The projection is derived, so it cannot drift from what the container holds."""
        result = ValidationResult('s1', [_finding(Severity.WARNING, 'advisory')])
        assert result.is_valid is True
        result.findings.append(_finding(Severity.ERROR, 'rejection'))
        assert result.is_valid is False and result.errors == ['rejection']


class TestFindingsCarryTheirOrigin:
    def test_check_and_domain_survive_on_the_finding(self):
        finding = _finding(Severity.WARNING, 'budget below granularity', check='budget_granularity')
        assert finding.check == 'budget_granularity'
        assert finding.domain is ValidationDomain.DATA
        assert finding.scope == 's1'

    def test_domain_is_a_closed_set(self):
        """A free string would let 'profiling' / 'Profiling' / 'perf' coexist and break filtering."""
        assert {d.value for d in ValidationDomain} >= {
            'config', 'data', 'broker', 'algo', 'execution',
            'setup', 'profiling', 'performance', 'portfolio', 'robustness'}


class TestTheExecutionGate:
    def test_a_rejected_scenario_stays_excluded(self):
        """§33: a config/data error excludes the scenario; the gate reads the derived flag."""
        scenario = SingleScenario(
            name='s1', scenario_index=0, symbol='BTCUSD', data_broker_type='kraken_spot',
            start_date=datetime(2025, 10, 13, tzinfo=timezone.utc))
        assert scenario.is_valid() is True

        scenario.validation_result.append(
            ValidationResult('s1', [_finding(Severity.WARNING, 'advisory only')]))
        assert scenario.is_valid() is True

        scenario.validation_result.append(
            ValidationResult('s1', [_finding(Severity.ERROR, 'no tick data')]))
        assert scenario.is_valid() is False
