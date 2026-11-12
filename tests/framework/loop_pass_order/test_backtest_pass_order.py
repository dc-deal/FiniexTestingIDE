"""
FiniexTestingIDE - Backtest Pass Order Tests

What the backtest's tick loop calls in each kind of pass, and in which order: the tick pass, the
tick the clipping gate stops, and the ghost pass a heartbeat logic gets between two ticks.

Pinned before the passes move behind one dispatcher (#458), which must not change behaviour. These
tests were written against the loop before the move and stay unchanged through it, so a step that
moves, disappears or arrives twice shows here first. When the passes take the phase order of the
event model (#460), the differences become deliberate and are written down here.

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

# A tick the algo sees: the clock moves to the tick, the day boundary is checked on it, the broker
# path and the bars see the tick, the bars it closed are read before the algo path consumes the
# render state, the account is valued, then the algo path runs. 'frame' already belongs to what the
# loop does after the pass.
_TICK_PASS = ['clock', 'boundary', 'on_tick', 'render', 'closed', 'equity', 'observe',
              'tracker', 'consume', 'algo', 'execute', 'drain', 'frame']
# The same without a decision event dispatcher (#348): nothing to drain
_TICK_PASS_WITHOUT_EVENTS = [step for step in _TICK_PASS if step != 'drain']
# A clipped tick still reaches the broker, the bars and the equity sample, and nothing after them
_CLIPPED_PASS = ['clock', 'boundary', 'on_tick', 'render', 'closed', 'equity', 'observe']
# A ghost pass whose decision acts, and one whose decision is None: drained before and after
_GHOST_PASS_ACTING = ['clock', 'boundary', 'heartbeat', 'drain', 'ghost', 'execute', 'drain',
                      'frame']
_GHOST_PASS_QUIET = ['clock', 'boundary', 'heartbeat', 'drain', 'ghost', 'drain', 'frame']
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
    steps.spy(TradeSimulator, 'on_tick', 'on_tick')
    steps.spy(TradeSimulator, 'heartbeat', 'heartbeat')
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

        assert backtest_steps.names == _TICK_PASS * 3 + _RUN_END

    def test_without_a_dispatcher_nothing_is_drained(self, backtest_steps):
        simulation_result(_ticks(3), spot_mode=False, orchestrator=backtest_steps.orchestrator(),
                          state_frames=backtest_steps.frames())

        assert backtest_steps.names == _TICK_PASS_WITHOUT_EVENTS * 3 + _RUN_END_WITHOUT_EVENTS


class TestClippedTick:

    def test_stops_after_the_equity_sample(self, backtest_steps):
        simulation_result(_clipped_in_the_middle(), spot_mode=False,
                          orchestrator=backtest_steps.orchestrator(), decision_events=True,
                          state_frames=backtest_steps.frames())

        assert backtest_steps.names == _TICK_PASS + _CLIPPED_PASS + _TICK_PASS + _RUN_END


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
            _TICK_PASS + _GHOST_PASS_ACTING + _GHOST_PASS_QUIET + _TICK_PASS + _RUN_END)

    def test_none_in_a_gap_no_longer_than_the_interval(self, backtest_steps):
        simulation_result(_ticks(2), spot_mode=False, orchestrator=backtest_steps.orchestrator(),
                          decision_events=True, state_frames=backtest_steps.frames(),
                          heartbeat_interval_ms=1000)

        assert backtest_steps.names == _TICK_PASS * 2 + _RUN_END


class TestProfiling:

    def test_keys_in_pass_order_and_counted_per_pass(self, backtest_steps):
        """The timers' keys keep the order the pass reaches them in; the reports read them."""
        result = simulation_result(_clipped_in_the_middle(), spot_mode=False,
                                   orchestrator=backtest_steps.orchestrator(),
                                   decision_events=True)

        profiling = result.profiling_data
        assert list(profiling.profile_times) == [
            'trade_simulator', 'bar_rendering', 'equity_sample', 'worker_decision',
            'order_execution', 'decision_events', 'live_update', 'total_per_tick']
        # Three ticks reach the broker path and the bars, two the algo path; the total per tick
        # is a time only
        assert list(profiling.profile_counts.items()) == [
            ('trade_simulator', 3), ('bar_rendering', 3), ('equity_sample', 3),
            ('worker_decision', 2), ('order_execution', 2), ('decision_events', 2),
            ('live_update', 2)]
