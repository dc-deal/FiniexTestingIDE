"""
FiniexTestingIDE - Backtest Pass Order Tests

What the backtest's timeline calls in each kind of instant, and in which order: a slice the algo
computes on, a slice whose ticks were all clipped, a slice of several ticks, and a heartbeat
logic's timer between two slices.

Pinned before the passes moved behind one dispatcher (#458), and unchanged through that move. With
the timeline driver (#460) the instants take the phase order of the event model, and these are
the differences, deliberate and written down here:
- the clock is set once per instant, and a tick no longer sets it again;
- a modify or cancel due by the instant applies before its first tick ('scheduled'), the latency
  queue resolves once after its last tick ('due') — no longer inside the first tick's broker path;
- the bars a slice closed are read after all its ticks, not after the first;
- the decision events are delivered before the algo as well as after it;
- a heartbeat instant resolves through the same two calls rather than its own heartbeat;
- a slice of several ticks applies each, and the algo computes once.

Every step is recorded by a spy on the CLASS of a collaborator the loop really uses, which calls
the real method, so the run behaves as it would without the spies. Only the orchestrator and the
state-frame recorder are doubles: the harness's orchestrator already is one, and the frame
recorder's position is all these tests need from it.
"""

import dataclasses
from typing import List

import pytest

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.core.test_probes.deterministic_probe import (
    DeterministicProbe,
)
from python.framework.process.market_data_episode_tracker import MarketDataEpisodeTracker
from python.framework.reporting.booking_period_recorder import BookingPeriodRecorder
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from tests.framework.loop_pass_order.conftest import PassSteps
from tests.shared.booking_day_harness import simulation_result, ticks_across_midnight

# A slice of one tick the algo sees: the clock moves to the instant, the day boundary is checked on
# it, what was scheduled applies, the tick reaches the broker path, the bars and the equity sample,
# the bars the slice closed are read, what came due resolves, the events are delivered, then the
# algo path runs and its events are delivered. 'frame' already belongs to what the loop does after
# the instant.
_TICK_SLICE = ['clock', 'boundary', 'scheduled', 'apply', 'render', 'equity', 'observe', 'closed',
               'due', 'drain', 'tracker', 'consume', 'algo', 'execute', 'drain', 'frame']
# The same without a decision event dispatcher (#348): nothing to drain
_TICK_SLICE_WITHOUT_EVENTS = [step for step in _TICK_SLICE if step != 'drain']
# A clipped slice still reaches the broker, the bars, the equity sample, the resolutions and the
# delivery, and nothing after them
_CLIPPED_SLICE = ['clock', 'boundary', 'scheduled', 'apply', 'render', 'equity', 'observe',
                  'closed', 'due', 'drain']
# A heartbeat instant whose decision acts, and one whose decision is None
_GHOST_ACTING = ['clock', 'boundary', 'scheduled', 'due', 'drain', 'ghost', 'execute', 'drain',
                 'frame']
_GHOST_QUIET = ['clock', 'boundary', 'scheduled', 'due', 'drain', 'ghost', 'drain', 'frame']
# After the last pass: the session-end event and the outcomes of its orders are each delivered,
# and the account is sampled once more
_RUN_END = ['drain', 'drain', 'equity']
_RUN_END_WITHOUT_EVENTS = ['equity']


@pytest.fixture
def backtest_steps(steps: PassSteps) -> PassSteps:
    """
    The step recorder with a spy on every collaborator a backtest pass calls.

    Args:
        steps: The suite's step recorder

    Returns:
        The recorder
    """
    steps.spy(TradeSimulator, 'set_current_time', 'clock')
    steps.spy(BookingPeriodRecorder, 'check_boundary', 'boundary')
    steps.spy(TradeSimulator, 'resolve_scheduled', 'scheduled')
    steps.spy(TradeSimulator, 'apply_tick', 'apply')
    steps.spy(TradeSimulator, 'resolve_due', 'due')
    steps.spy(BarRenderingController, 'process_tick', 'render')
    steps.spy(BarRenderingController, 'get_pending_closed_timeframes', 'closed')
    steps.spy(BarRenderingController, 'consume_bar_render_state', 'consume')
    steps.spy(PortfolioManager, 'sample_equity', 'equity')
    steps.spy(BookingPeriodRecorder, 'observe_tick', 'observe')
    steps.spy(MarketDataEpisodeTracker, 'on_tick', 'tracker')
    steps.spy(DeterministicProbe, 'execute_decision', 'execute')
    steps.spy(DecisionEventDispatcher, 'drain', 'drain')
    return steps


def _ticks(count: int) -> List[TickData]:
    """
    Flat ticks one second apart, all before the harness's day boundary.

    Args:
        count: How many

    Returns:
        The ticks, oldest first
    """
    return ticks_across_midnight()[:count]


def _clipped_in_the_middle() -> List[TickData]:
    """
    Three ticks, the second of them clipped.

    Returns:
        The ticks, oldest first
    """
    ticks = _ticks(3)
    ticks[1] = dataclasses.replace(ticks[1], is_clipped=True)
    return ticks


class TestTickPass:

    def test_clock_boundary_broker_bars_equity_then_algo_execute_drain(self, backtest_steps):
        simulation_result(_ticks(3), spot_mode=False, orchestrator=backtest_steps.orchestrator(),
                          decision_events=True, state_frames=backtest_steps.frames())

        assert backtest_steps.names == _TICK_SLICE * 3 + _RUN_END

    def test_without_a_dispatcher_nothing_is_drained(self, backtest_steps):
        simulation_result(_ticks(3), spot_mode=False, orchestrator=backtest_steps.orchestrator(),
                          state_frames=backtest_steps.frames())

        assert backtest_steps.names == _TICK_SLICE_WITHOUT_EVENTS * 3 + _RUN_END_WITHOUT_EVENTS


class TestTheIndicatorSeries:
    """#379: the series is recorded right after the frame, and alone it still reads the bars."""

    def test_recorded_right_after_the_frame_on_a_tick_pass(self, backtest_steps):
        simulation_result(_ticks(3), spot_mode=False, orchestrator=backtest_steps.orchestrator(),
                          decision_events=True, state_frames=backtest_steps.frames(),
                          indicator_series=backtest_steps.series())

        assert backtest_steps.names == (_TICK_SLICE + ['series']) * 3 + _RUN_END

    def test_without_state_frames_the_closed_bars_are_still_read(self, backtest_steps):
        simulation_result(_ticks(3), spot_mode=False, orchestrator=backtest_steps.orchestrator(),
                          decision_events=True, indicator_series=backtest_steps.series())

        tick_pass = [step for step in _TICK_SLICE if step != 'frame'] + ['series']
        assert backtest_steps.names == tick_pass * 3 + _RUN_END


class TestSliceOfSeveralTicks:

    def test_every_tick_reaches_the_broker_and_the_bars_and_the_algo_computes_once(
            self, backtest_steps):
        """Two ticks arrive in one millisecond, a third a second later: two instants (#460)."""
        simulation_result(self._two_in_one_millisecond(), spot_mode=False,
                          orchestrator=backtest_steps.orchestrator(), decision_events=True,
                          state_frames=backtest_steps.frames())

        one_tick = ['apply', 'render', 'equity', 'observe']
        two_ticks = (_TICK_SLICE[:3] + one_tick * 2 + _TICK_SLICE[7:])
        assert backtest_steps.names == two_ticks + _TICK_SLICE + _RUN_END

    def test_the_algo_computes_on_the_slice_s_last_tick_for_both(self, backtest_steps):
        """
        The algo meets the most recent state (740.a): the second of the two ticks, standing for
        both of them.
        """
        ticks = self._two_in_one_millisecond()
        simulation_result(ticks, spot_mode=False, orchestrator=backtest_steps.orchestrator(),
                          decision_events=True)

        computed = [(tick.bid, weight) for tick, weight in backtest_steps.algo_ticks]
        assert computed == [(ticks[1].bid, 2), (ticks[2].bid, 1)]

    @staticmethod
    def _two_in_one_millisecond() -> List[TickData]:
        """
        Three ticks, the first two arriving in one millisecond at different prices.

        Returns:
            The ticks, oldest first
        """
        ticks = _ticks(3)
        ticks[1] = dataclasses.replace(ticks[1], timestamp=ticks[0].timestamp,
                                       time_msc=ticks[0].time_msc,
                                       collected_msc=ticks[0].collected_msc,
                                       bid=ticks[0].bid + 0.5, ask=ticks[0].ask + 0.5)
        return ticks


class TestClippedTick:

    def test_stops_after_the_delivery(self, backtest_steps):
        simulation_result(_clipped_in_the_middle(), spot_mode=False,
                          orchestrator=backtest_steps.orchestrator(), decision_events=True,
                          state_frames=backtest_steps.frames())

        assert backtest_steps.names == _TICK_SLICE + _CLIPPED_SLICE + _TICK_SLICE + _RUN_END


class TestGhostPass:

    @pytest.fixture(autouse=True)
    def _wants_heartbeats(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        The probe opts in to heartbeats, as a heartbeat logic does.

        Args:
            monkeypatch: pytest's patcher
        """
        monkeypatch.setattr(DeterministicProbe, 'wants_heartbeat', lambda logic: True)

    def test_clock_boundary_heartbeat_drain_ghost_execute_drain(self, backtest_steps):
        """Two ticks a second apart, a heartbeat every 400 ms: two ghost instants between them."""
        acting = Decision(action=DecisionLogicAction.FLAT)

        simulation_result(_ticks(2), spot_mode=False,
                          orchestrator=backtest_steps.orchestrator([acting]),
                          decision_events=True, state_frames=backtest_steps.frames(),
                          heartbeat_interval_ms=400)

        assert backtest_steps.names == (
            _TICK_SLICE + _GHOST_ACTING + _GHOST_QUIET + _TICK_SLICE + _RUN_END)

    def test_none_in_a_gap_no_longer_than_the_interval(self, backtest_steps):
        simulation_result(_ticks(2), spot_mode=False, orchestrator=backtest_steps.orchestrator(),
                          decision_events=True, state_frames=backtest_steps.frames(),
                          heartbeat_interval_ms=1000)

        assert backtest_steps.names == _TICK_SLICE * 2 + _RUN_END


class TestProfiling:

    def test_keys_in_pass_order_and_counted_per_pass(self, backtest_steps):
        """The timers' keys keep the order the pass reaches them in; the reports read them."""
        result = simulation_result(_clipped_in_the_middle(), spot_mode=False,
                                   orchestrator=backtest_steps.orchestrator(),
                                   decision_events=True)

        profiling = result.profiling_data
        assert list(profiling.profile_times) == [
            'trade_simulator', 'bar_rendering', 'equity_sample', 'decision_events',
            'worker_decision', 'order_execution', 'live_update', 'total_per_tick',
            'total_per_algo_pass']
        # Three ticks reach the broker path and the bars, two the algo path; the events are
        # delivered once in every instant and once more after each algo pass; the total per tick
        # is a time only, the total of the algo passes is counted per pass (#460)
        assert list(profiling.profile_counts.items()) == [
            ('trade_simulator', 3), ('bar_rendering', 3), ('equity_sample', 3),
            ('decision_events', 5), ('worker_decision', 2), ('order_execution', 2),
            ('live_update', 2), ('total_per_algo_pass', 2)]
