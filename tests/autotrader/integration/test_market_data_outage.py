"""
FiniexTestingIDE - AutoTrader Market-Data Outage Integration Test (#436)

One fast combined session (market_data_outage_test.json): the mock feeder
freezes mid-replay (freeze lever) → the idle heartbeat flips the session-level
staleness contract (pot warning + mandatory on_market_data_stale + OrderGuard
entry block, probed via one deliberate ghost-pass entry) → ticks resume →
recovery span reaches the pot. The aged sentiment archive additionally fires
the #434 signal-side hook on the first result — both staleness contracts in
one session ("runs fine → silence → notified → recovered").
"""


import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.log_level import LogLevel
from tests.shared.fixture_helpers import logged_messages, remove_run_dir

OUTAGE_PROFILE = 'configs/autotrader_profiles/backtesting/market_data_outage_test.json'


@pytest.fixture(scope='module')
def outage_session():
    """Run the combined outage session once, shared across all tests."""
    config = load_autotrader_config(OUTAGE_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    yield result
    remove_run_dir(trader._run_dir)


def _count(result, needle: str) -> int:
    return sum(1 for w in logged_messages(result, LogLevel.WARNING) if needle in w)


class TestMarketDataOutage:
    """Tick-side staleness contract, end to end on the live loop."""

    def test_session_completes_normally(self, outage_session):
        result = outage_session
        assert result.shutdown_mode == 'normal'
        assert result.ticks_processed == 3000
        assert len(logged_messages(result, LogLevel.ERROR)) == 0, (
            f'Unexpected errors: {logged_messages(result, LogLevel.ERROR)[:5]}')

    def test_stale_episode_reaches_the_pot_with_span(self, outage_session):
        """One freeze → exactly one flip warning + one recovery span line."""
        result = outage_session
        assert _count(result, 'Market data stale since') == 1
        assert _count(result, 'Market data recovered') == 1
        # Episode duration is a wall-axis measurement — never negative, even
        # though mock replay tick timestamps and wall heartbeat time diverge.
        recovery = next(
            w for w in logged_messages(result, LogLevel.WARNING) if 'Market data recovered' in w)
        assert '(-' not in recovery

    def test_decision_hook_fired_once(self, outage_session):
        """Edge-triggered: the mandatory hook fires once per episode."""
        result = outage_session
        assert _count(result, '[PROBE] on_market_data_stale fired') == 1

    def test_guard_blocked_the_stale_entry(self, outage_session):
        """The deliberate ghost-pass entry was rejected (framework floor)."""
        result = outage_session
        assert _count(result, '[PROBE] stale entry rejected') == 1
        assert result.execution_stats.orders_rejected >= 1

    def test_signal_side_fired_too(self, outage_session):
        """Aged archive → #434 signal hook on the first result (both sides)."""
        result = outage_session
        assert _count(result, '[PROBE] on_signal_stale fired') == 1

    def test_the_stress_config_reaches_the_session_validation_channel(self, outage_session):
        """
        A stressed session must be distinguishable from a clean one — the live half of the
        rule the sim batch has always honoured. This profile carries an active
        stale_data_stress, so the run's own validation channel must say so.
        """
        findings = [f for vr in outage_session.session_validation_result for f in vr.findings]
        assert 'stress_test' in [f.check for f in findings], (
            f'No stress advisory in the channel: {[f.check for f in findings]}')
