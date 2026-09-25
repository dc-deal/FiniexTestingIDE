"""
FiniexTestingIDE - AutoTrader Planned Tick-Outage Drill (#444)

One session over `tick_outage_stress_test.json`, which declares a single stale window on
its TICK source. Until #444 that window was expressible on an AutoTrader profile and driven
by nobody — the status-plane driver lived in the simulation tick loop alone, so the one
market-data drill the live loop could rehearse was the transport-real freeze.

The contract under test is the one the simulation has always honoured: the ticks KEEP
FLOWING and only the feed STATUS goes stale. A dead feed does not freeze the market, and
carving the ticks instead would also freeze broker-side SL/TP resolution.

Deliberately its own profile rather than a second window on `market_data_outage_test`: that
session already produces a wall-clock episode, and a second one in the same counts cannot be
told from it. Here the ONLY episode is the injected one — `market_data_stale_after_s` is set
so high that the wall clock cannot speak at all.
"""


import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.disturbance_episode_types import DisturbanceOrigin
from python.framework.types.log_level import LogLevel
from tests.shared.fixture_helpers import logged_messages, remove_run_dir

STRESS_PROFILE = 'configs/autotrader_profiles/backtesting/tick_outage_stress_test.json'

WINDOW_LABEL = 'tick feed status stale 10min'


@pytest.fixture(scope='module')
def stress_session():
    """Run the planned tick-outage session once, shared across all tests."""
    config = load_autotrader_config(STRESS_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    yield result
    remove_run_dir(trader._run_dir)


def _count(result, needle: str) -> int:
    return sum(1 for w in logged_messages(result, LogLevel.WARNING) if needle in w)


class TestPlannedTickOutage:
    """The status-plane window, driven by the live loop."""

    def test_session_completes_normally(self, stress_session):
        result = stress_session
        assert result.shutdown_mode == 'normal'
        assert result.ticks_processed == 3000
        assert len(logged_messages(result, LogLevel.ERROR)) == 0, (
            f'Unexpected errors: {logged_messages(result, LogLevel.ERROR)[:5]}')

    def test_the_window_flipped_the_status_and_recovered(self, stress_session):
        """Both edges, once each — the drill is a window, not a permanent state."""
        result = stress_session
        assert _count(result, '[STRESS] Market data stale since') == 1
        assert _count(result, '[STRESS] Market data recovered') == 1

    def test_the_mandatory_hook_fired(self, stress_session):
        """
        The whole reason the drill exists: an algo's `on_market_data_stale` is now
        rehearsable on the live loop, deterministically, without waiting for a real outage.
        """
        result = stress_session
        assert _count(result, '[PROBE] on_market_data_stale fired') == 1

    def test_the_guard_blocked_an_entry_inside_the_window(self, stress_session):
        """The framework floor: no new risk on a feed we do not trust."""
        result = stress_session
        assert _count(result, '[PROBE] stale entry rejected') == 1
        assert result.execution_stats.orders_rejected >= 1

    def test_the_ticks_kept_flowing_while_the_status_was_stale(self, stress_session):
        """
        The contract that separates this drill from the freeze lever, and the one number
        that proves it: ticks were COUNTED as stale rather than absent. A carve would show
        zero stale ticks and a gap in the stream instead.
        """
        stats = stress_session.market_data_tick_stats
        assert stats is not None
        assert stats.stale_ticks > 0, (
            'no tick was observed inside the window — the feed was cut, not flagged')
        assert stats.fresh_ticks > 0
        assert stats.stale_ticks + stats.fresh_ticks == 3000

    def test_the_episode_is_recorded_as_injected(self, stress_session):
        """
        A drill in the record must never read as a venue fault. The origin and the label
        are what a reader has to tell them apart by.
        """
        episodes = stress_session.disturbance_episodes
        assert len(episodes) == 1, f'expected exactly one episode, got {episodes}'
        assert episodes[0].origin == DisturbanceOrigin.STRESS_INJECTED
        assert WINDOW_LABEL in episodes[0].label

    def test_the_stress_config_reaches_the_session_validation_channel(self, stress_session):
        """A stressed session must be distinguishable from a clean one (§35, Tier 1)."""
        findings = [f for vr in stress_session.session_validation_result for f in vr.findings]
        stress = [f for f in findings if f.check == 'stress_test']
        assert stress, f'No stress advisory in the channel: {[f.check for f in findings]}'
        assert WINDOW_LABEL in stress[0].message, (
            f'the advisory does not name the planned window: {stress[0].message}')
