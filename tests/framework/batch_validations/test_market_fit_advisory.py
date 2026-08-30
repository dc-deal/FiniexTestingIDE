"""
Market-Fit Advisory Tests (#118 Stage 0).

The advisory used to be a `logger.warning` inside the scenario SUBPROCESS. Two things were
wrong with that, and these tests pin both fixes:

- **Channel.** It is a verdict ("this algo was not designed for here"), so the run report must
  classify it as validator-produced. As a log line it arrived as Tier 2 — literally "an
  observation nobody adjudicated" — while the code producing it lived in `validators/`.
- **Time.** Every input is static config: `get_metadata()` is a classmethod, `broker_type` and
  `symbol` come from the scenario. Nothing about it needed the run, yet it took a subprocess to
  learn that a scenario was mismatched, and the finding then had to travel back out.

It now runs in Phase 0 of the mount and lands on `SingleScenario.validation_result`, which the
report already reads. The version LINE stayed behind as a log call, because that half really is
an observation — see `test_the_two_halves_stayed_apart`.
"""

from datetime import datetime, timezone

from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.broker_config import BrokerType
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import Severity, ValidationDomain
from python.framework.validators import component_metadata_advisory as advisory
from python.framework.validators.component_metadata_advisory import check_market_fit
from python.framework.validators.scenario_validator import ScenarioValidator

_DT = datetime(2026, 4, 27, tzinfo=timezone.utc)
# CORE/trend_channel_reference declares recommended_markets=('forex',) — on a crypto broker
# that is a mismatch, and it is a real registered logic, so the factory resolves it.
_FOREX_LOGIC = 'CORE/trend_channel_reference'
_CRYPTO_LOGIC = 'CORE/hybrid_sentiment_reference'


def _scenario(logic_type: str, symbol: str = 'BTCUSD') -> SingleScenario:
    """A scenario as Phase 0 sees it — broker_type already assigned by BrokerDataPreparator."""
    s = SingleScenario(name='probe', scenario_index=0, symbol=symbol,
                       data_broker_type='kraken_spot', start_date=_DT)
    s.broker_type = BrokerType.KRAKEN_SPOT
    s.strategy_config = {'decision_logic_type': logic_type}
    return s


def _findings(scenario: SingleScenario) -> list:
    ScenarioValidator.validate_market_fit([scenario], GlobalLogger())
    return [f for vr in scenario.validation_result for f in vr.findings]


class TestTheVerdictIsAFindingNotALogLine:
    def test_a_mismatch_produces_an_advisory_finding(self):
        findings = _findings(_scenario(_FOREX_LOGIC))
        assert [f.check for f in findings] == ['market_fit']
        assert findings[0].domain is ValidationDomain.ALGO
        assert 'recommends markets' in findings[0].message

    def test_it_never_excludes_the_scenario(self):
        """An advisory is not a rejection — §33: only errors take a scenario out of the run."""
        scenario = _scenario(_FOREX_LOGIC)
        _findings(scenario)
        assert all(vr.is_valid for vr in scenario.validation_result)
        assert all(f.severity is Severity.WARNING for f in _findings(scenario))

    def test_a_matching_market_produces_nothing(self):
        assert _findings(_scenario(_CRYPTO_LOGIC)) == []

    def test_the_finding_names_its_scenario(self):
        """Scope is what lets the report say WHICH unit the advisory is about."""
        assert _findings(_scenario(_FOREX_LOGIC))[0].scope == 'probe'


class TestItAnswersWithoutTheRun:
    """
    The whole point of the move: no subprocess, no instance, no ProcessResult transport.
    `get_metadata()` is a classmethod, so Phase 0 can resolve the class and ask.
    """

    def test_no_component_is_instantiated(self):
        _findings(_scenario(_FOREX_LOGIC))  # would raise if construction were required

    def test_an_unresolvable_logic_is_skipped_not_reported(self):
        """Resolution failures belong to the requirements collector — never a false advisory."""
        assert _findings(_scenario('CORE/does_not_exist')) == []

    def test_a_scenario_without_a_decision_logic_is_skipped(self):
        scenario = _scenario(_FOREX_LOGIC)
        scenario.strategy_config = {}
        assert _findings(scenario) == []


class TestTheCheckItself:
    """The pure function, so the two callers (sim Phase 0, live startup) share one formula."""

    @staticmethod
    def _meta(markets=(), instruments=()) -> ComponentMetadata:
        return ComponentMetadata(
            version='1.0', recommended_markets=markets, recommended_instruments=instruments)

    def test_no_recommendation_means_no_opinion(self):
        assert check_market_fit(self._meta(), 'x', 'kraken_spot', 'BTCUSD', 's') == []

    def test_instrument_mismatch_is_its_own_finding(self):
        findings = check_market_fit(
            self._meta(instruments=('ETHUSD',)), 'x', 'kraken_spot', 'BTCUSD', 's')
        assert len(findings) == 1 and 'recommends instruments' in findings[0].message

    def test_both_mismatches_yield_both_findings(self):
        findings = check_market_fit(
            self._meta(markets=('forex',), instruments=('EURUSD',)),
            'x', 'kraken_spot', 'BTCUSD', 's')
        assert len(findings) == 2

    def test_an_unknown_broker_is_silent_rather_than_fatal(self):
        """It must never break a run: an unresolvable market type skips the market half."""
        assert check_market_fit(
            self._meta(markets=('forex',)), 'x', 'not_a_broker', 'BTCUSD', 's') == []


def test_the_two_halves_stayed_apart():
    """
    The observation (version line) and the verdict (market fit) are separate functions now.
    Pinned because merging them back would silently re-route the verdict into the log pot.
    """
    assert hasattr(advisory, 'surface_decision_logic_version')
    assert not hasattr(advisory, 'surface_decision_logic_metadata')
