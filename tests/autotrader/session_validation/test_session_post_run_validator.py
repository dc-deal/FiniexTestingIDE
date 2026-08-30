"""
Session Post-Run Validator Tests.

A live session used to have no validation channel at all: `WarningsErrorsReport` could carry
Tier-2 rows (the log pot) and nothing else. The visible consequence was that an ACTIVE stress
config produced no `STRESS TEST ACTIVE` warning live, although the project rule states without
qualification that every active stress config surfaces as a Tier-1 warning — so a stressed live
session was indistinguishable from a clean one.

These tests pin the channel: the validator produces the findings, and they arrive in the report
as Tier-1 rows carrying their own origin. The stress check is SHARED with the sim batch
(`shared_advisory_checks`), so the sim-side tests pin the same formula from the other end.
"""

from datetime import datetime, timezone

from python.framework.reporting.builders.warnings_errors_report_builder import (
    build_warnings_errors_report_from_session,
)
from python.framework.types.api.report_types import WarningTier
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.autotrader_types.clipping_monitor_types import (
    ClippingSessionSummary,
)
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.config_types.scenario_settings_config_types import (
    ScenarioSettingsConfig,
)
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.types.validation_types import ValidationDomain, ValidationResult
from python.framework.validators.component_metadata_advisory import check_market_fit
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


class TestTheClippingAdvisory:
    """
    The one performance verdict a live session can honestly make. It exists BECAUSE the fixed
    per-component threshold was removed: a ratio is measured against real tick arrival, so it
    says how often the algo failed to keep up — where an absolute millisecond figure could not.
    The line itself is a policy question, so it comes from config, never a constant.
    """

    @staticmethod
    def _clipped(ratio: float) -> AutoTraderResult:
        return AutoTraderResult(clipping_summary=ClippingSessionSummary(
            total_ticks=1000, ticks_clipped=int(1000 * ratio), clipping_ratio=ratio,
            max_stale_ms=340.5, avg_processing_ms=2.1))

    def test_a_ratio_above_the_limit_is_flagged(self):
        findings = _validated(self._clipped(0.12), _config())
        assert [f.check for f in findings] == ['clipping']
        assert findings[0].domain is ValidationDomain.PROFILING
        # The numbers that make it actionable travel with the verdict.
        assert '12.0%' in findings[0].message and '120/1000' in findings[0].message
        assert '340.5ms' in findings[0].message

    def test_a_ratio_below_the_limit_is_not(self):
        assert _validated(self._clipped(0.03), _config()) == []

    def test_the_limit_itself_is_not_exceeded(self):
        """`>` not `>=` — sitting exactly on the configured line is still within it."""
        config = _config()
        assert _validated(
            self._clipped(config.clipping_monitor.warn_above_ratio), config) == []

    def test_the_threshold_comes_from_config_not_a_constant(self):
        """Tightening the profile must change the verdict — otherwise the knob is decoration."""
        config = _config()
        config.clipping_monitor.warn_above_ratio = 0.01
        assert len(_validated(self._clipped(0.03), config)) == 1

    def test_a_ratio_of_one_disables_it(self):
        """A ratio can never exceed 1.0, so that is the documented off switch."""
        config = _config()
        config.clipping_monitor.warn_above_ratio = 1.0
        assert _validated(self._clipped(1.0), config) == []

    def test_a_session_with_no_ticks_says_nothing(self):
        """Zero ticks is not zero clipping — it is no measurement at all."""
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
        result = AutoTraderResult(session_logger_buffer=[
            LogRecord(level=LogLevel.WARNING, timestamp=datetime.now(timezone.utc),
                      scope='s', message='something odd happened')])
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


class TestAStartupFindingSurvivesToTheReport:
    """
    Market fit is decided at STARTUP — a live operator must see it before the first trade —
    but the validation channel lives on the result, which does not exist yet. AutotraderMain
    therefore holds the findings and hands them over in `_collect_results`. This pins the
    hand-over shape, so the held finding cannot quietly stop arriving.
    """

    def test_a_held_finding_reaches_the_report_as_tier_1(self):
        finding = check_market_fit(
            ComponentMetadata(version='1.0', recommended_markets=('forex',)),
            'my_algo', 'kraken_spot', 'BTCUSD', 'live_probe')[0]

        result = AutoTraderResult()
        result.add_session_validation_result(ValidationResult(finding.scope, [finding]))

        report = build_warnings_errors_report_from_session(result, 'live_probe', 'BTCUSD')
        rows = [w for w in report.warnings if w.tier == WarningTier.VALIDATOR_PRODUCED]
        assert len(rows) == 1
        assert rows[0].check == 'market_fit' and rows[0].domain == 'algo'
        assert rows[0].scope == 'live_probe'

    def test_the_default_is_empty_so_a_startup_abort_still_reports(self):
        """A session that dies before the check must not lose its report to an empty channel."""
        report = build_warnings_errors_report_from_session(
            AutoTraderResult(), 'live_probe', 'BTCUSD')
        assert report.warnings == []


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
