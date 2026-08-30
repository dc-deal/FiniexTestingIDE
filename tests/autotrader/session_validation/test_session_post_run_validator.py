"""
Session Post-Run Validator Tests.

A live session used to have no validation channel at all: `WarningsErrorsReport` could carry
Tier-2 rows (the log pot) and nothing else. The visible consequence was that an ACTIVE stress
config produced no `STRESS TEST ACTIVE` warning live, although the project rule states without
qualification that every active stress config surfaces as a Tier-1 warning — so a stressed live
session was indistinguishable from a clean one.

These tests pin the channel: the validator produces the findings, and they arrive in the report
as Tier-1 rows carrying their own origin. The two checks are SHARED with the sim batch
(`shared_advisory_checks`), so the sim-side tests pin the same formulas from the other end.
"""

import pytest

from python.framework.reporting.builders.warnings_errors_report_builder import (
    build_warnings_errors_report_from_session,
)
from python.framework.types.api.report_types import WarningTier
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.config_types.scenario_settings_config_types import (
    ScenarioSettingsConfig,
)
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats,
    WorkerPerformanceStats,
)
from python.framework.validators.session_post_run_validator import SessionPostRunValidator
from python.framework.validators.shared_advisory_checks import check_stress_test

_STRESS = {
    'stale_data_stress': {
        'enabled': True,
        'events': [{
            'label': 'sentiment feed dies 60min',
            'data_source': 'crypto_sentiment_mock',
            'stale_start_date': '2026-04-27T06:10:00+00:00',
            'stale_end_date': '2026-04-27T07:10:00+00:00',
        }],
    },
}


def _config(stress=None, with_settings=True) -> AutoTraderConfig:
    """A profile config; `with_settings=False` is a real live session (no mock replay)."""
    settings = None
    if with_settings:
        settings = ScenarioSettingsConfig(start_date='2026-04-27', stress_test_config=stress)
    return AutoTraderConfig(
        name='live_probe', symbol='BTCUSD', broker_type='kraken_spot',
        scenario_settings=settings)


def _worker(name: str, avg_ms: float) -> WorkerPerformanceStats:
    return WorkerPerformanceStats(
        worker_type=f'CORE/{name}', worker_name=name, worker_call_count=10,
        worker_total_time_ms=avg_ms * 10, worker_avg_time_ms=avg_ms,
        worker_min_time_ms=avg_ms, worker_max_time_ms=avg_ms)


def _validated(result: AutoTraderResult, config: AutoTraderConfig) -> list:
    """Run the validator and return the flat list of findings it recorded."""
    SessionPostRunValidator(result, config).validate()
    return [f for vr in result.session_validation_result for f in vr.findings]


class TestTheStressWarningReachesALiveSession:
    """The rule is pipeline-independent: an active stress config is always a Tier-1 warning."""

    def test_an_active_stress_config_is_flagged(self):
        findings = _validated(AutoTraderResult(), _config(stress=_STRESS))
        assert [f.check for f in findings] == ['stress_test']
        assert 'STRESS TEST ACTIVE' in findings[0].message
        assert 'sentiment feed dies 60min' in findings[0].message

    def test_the_message_names_the_session_not_a_scenario(self):
        """Live runs one profile — the sim's 'Scenarios (N)' would be the wrong word."""
        findings = _validated(AutoTraderResult(), _config(stress=_STRESS))
        assert 'Session (1): live_probe' in findings[0].message
        assert 'Scenarios' not in findings[0].message

    def test_a_disabled_stress_config_is_not_flagged(self):
        disabled = {'stale_data_stress': {'enabled': False, 'events': []}}
        assert _validated(AutoTraderResult(), _config(stress=disabled)) == []

    def test_no_stress_config_is_not_flagged(self):
        assert _validated(AutoTraderResult(), _config()) == []

    def test_a_session_without_scenario_settings_is_not_an_error(self):
        """A real live session has no mock-replay settings at all — that is not a finding."""
        assert _validated(AutoTraderResult(), _config(with_settings=False)) == []


class TestSlowComponentsAreFlagged:
    """The same threshold the sim applies, over the session's own statistics."""

    def test_a_slow_worker_is_flagged(self):
        result = AutoTraderResult(worker_statistics=[_worker('heavy', 2.0)])
        findings = _validated(result, _config())
        assert [f.check for f in findings] == ['slow_component']
        assert "'heavy' averages 2.000ms" in findings[0].message

    def test_a_fast_worker_is_not(self):
        result = AutoTraderResult(worker_statistics=[_worker('light', 0.2)])
        assert _validated(result, _config()) == []

    def test_a_slow_decision_logic_is_flagged(self):
        result = AutoTraderResult(decision_statistics=DecisionLogicStats(
            decision_logic_name='tunnel', decision_avg_time_ms=3.5))
        findings = _validated(result, _config())
        assert [f.check for f in findings] == ['slow_component']
        assert 'decision logic' in findings[0].message and 'tunnel' in findings[0].message

    def test_a_session_with_no_statistics_is_not_flagged(self):
        """A session that never ran a worker must not report the emptiness as a problem."""
        assert _validated(AutoTraderResult(), _config()) == []


class TestTheChannelReachesTheReport:
    """The findings are only worth producing if the report actually carries them."""

    @staticmethod
    def _report(result: AutoTraderResult, config: AutoTraderConfig):
        SessionPostRunValidator(result, config).validate()
        return build_warnings_errors_report_from_session(result, config.name, config.symbol)

    def test_a_finding_becomes_a_tier_1_row_with_its_origin(self):
        report = self._report(AutoTraderResult(), _config(stress=_STRESS))
        major = [w for w in report.warnings if w.tier == WarningTier.VALIDATOR_PRODUCED]
        assert len(major) == 1
        row = major[0]
        assert row.check == 'stress_test' and row.domain == 'setup' and row.scope == 'run'

    def test_the_log_pot_still_arrives_as_tier_2(self):
        """Adding Tier 1 must not displace the channel that was already there."""
        result = AutoTraderResult(warning_messages=['something odd happened'])
        report = self._report(result, _config(stress=_STRESS))
        tiers = [w.tier for w in report.warnings]
        assert tiers.count(WarningTier.VALIDATOR_PRODUCED) == 1
        assert tiers.count(WarningTier.LOGGER_PRODUCED) == 1
        # A pot row claims no assertion — the tier already names its channel.
        pot = next(w for w in report.warnings if w.tier == WarningTier.LOGGER_PRODUCED)
        assert pot.check == '' and pot.domain == ''

    def test_a_clean_session_reports_nothing(self):
        report = self._report(AutoTraderResult(), _config())
        assert report.warnings == [] and report.errors == []


class TestTheSharedChecksProduceOneFormula:
    """
    Sim and live must not drift apart on a check they share. The formula lives in
    `shared_advisory_checks`; this pins that both callers reach the same text for the same
    input, which is the property a copy would have silently lost.
    """

    def test_the_stress_message_differs_only_in_the_unit_label(self):
        live = check_stress_test([('live_probe', _STRESS)], 'Session')
        sim = check_stress_test([('live_probe', _STRESS)], 'Scenarios')
        assert live.message.replace('Session (1)', 'X') == sim.message.replace(
            'Scenarios (1)', 'X')

    @pytest.mark.parametrize('avg_ms,expected', [(1.0, 0), (1.001, 1)])
    def test_the_threshold_is_exclusive(self, avg_ms, expected):
        """Exactly at the threshold is not 'over' it — the sim boundary, restated live."""
        result = AutoTraderResult(worker_statistics=[_worker('edge', avg_ms)])
        assert len(_validated(result, _config())) == expected
