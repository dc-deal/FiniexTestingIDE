"""
FiniexTestingIDE - AutoTrader Pass Order Tests

What the AutoTrader event loop calls in each kind of pass, and in which order: the tick pass, the
idle pass when no tick came within the heartbeat, and the pass a stop's wait makes while orders are
still on their way.

Pinned before the passes moved behind one dispatcher (#458), which changed no behaviour. Since the
session's timeline driver (#461) every instant runs in the phase order of the event model, as a
backtest's does; the differences to the order before it are deliberate and written down at each
pass below, and in the suite's documentation.

Every step is recorded by a spy on the CLASS of a collaborator the loop really uses, which calls
the real method. Where a step is the loop's own work — the signal inbox, the sealed days, the
account value, a rejection, the reconcile, the staleness, the persistence — the spy sits on the
loop's private method that does it: the dispatcher's hooks keep calling those, so a rename there
is one this test has to follow on purpose. Only the orchestrator and the state-frame recorder are
doubles.
"""

from datetime import datetime, timedelta
from typing import Any, List, Optional, Set

import pytest

from python.framework.autotrader import autotrader_event_loop as event_loop_module
from python.framework.autotrader.autotrader_event_loop import AutotraderEventLoop
from python.framework.autotrader.safety.safety_circuit_breaker import SafetyCircuitBreaker
from python.framework.autotrader.session_log_rotator import SessionLogRotator
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.core.test_probes.deterministic_probe import (
    DeterministicProbe,
)
from python.framework.process.market_data_episode_tracker import MarketDataEpisodeTracker
from python.framework.reporting.booking_period_recorder import BookingPeriodRecorder
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.decision_event_types import DecisionEventType
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)
from tests.framework.loop_pass_order.conftest import PassSteps
from tests.shared.booking_day_harness import (
    LOTS,
    SYMBOL,
    autotrader_loop,
    live_executor,
    ticks_across_midnight,
)

# A slice (#461), in the phase order: the clock moves to the instant and the day boundary is
# checked; each tick reaches the broker path, renders the bars, and the market status and the
# account value follow (before #461: the inbox first, the bars after the account); then the
# signal inbox; then what the venue answered resolves, once, after the ticks (before: inside the
# tick's broker path); the decision events are delivered BEFORE the algo too (before: only after
# it); the algo, the safety sample, the FLAT override's question, the execute and the trailing
# drain. Reconcile and persistence by the tick count, the carry-over and 'frame' follow the
# instant.
_TICK_PASS = ['clock', 'boundary', 'sealed_days', 'log_rotation', 'apply_tick', 'render',
              'tracker', 'account_value', 'observe', 'equity', 'signal_inbox', 'resolve_due',
              'drain', 'algo', 'equity', 'safety', 'entries_blocked', 'execute',
              'record_rejection', 'drain', 'reconcile', 'persist_state', 'carry_over', 'frame']
# A quiet heartbeat (#461): the clock moves, the signal inbox comes before the resolutions (before:
# after the staleness), the venue is heard through the heartbeat's resolution, then the staleness,
# the drain, the ghost pass — and a drain after it (before: none). The reconcile and the state
# persistence run on timers of their own (before: on every idle pass, behind their gates).
_IDLE_PASS_ACTING = ['clock', 'boundary', 'sealed_days', 'log_rotation', 'signal_inbox',
                     'resolve_due', 'heartbeat', 'staleness', 'drain', 'ghost', 'entries_blocked',
                     'execute', 'record_rejection', 'drain', 'carry_over', 'frame']
_IDLE_PASS_QUIET = ['clock', 'boundary', 'sealed_days', 'log_rotation', 'signal_inbox',
                    'resolve_due', 'heartbeat', 'staleness', 'drain', 'ghost', 'drain',
                    'carry_over', 'frame']
# A stop's wait: the orders' side of an idle pass, and nothing for the algo. What it settles is
# heard before the session-end event and not after it.
_WAIT_PASS_HEARD = ['clock', 'boundary', 'sealed_days', 'log_rotation', 'heartbeat', 'drain',
                    'carry_over']
_WAIT_PASS_UNHEARD = ['clock', 'boundary', 'sealed_days', 'log_rotation', 'heartbeat',
                      'carry_over']
# The session-end event, and the outcomes of the orders sent from it, are each delivered
_SESSION_END_EVENT = ['drain']
_SESSION_END_ORDERS = ['drain']


@pytest.fixture
def live_steps(steps: PassSteps) -> PassSteps:
    """
    The step recorder with a spy on every collaborator an AutoTrader pass calls.

    Args:
        steps: The suite's step recorder

    Returns:
        The recorder
    """
    steps.spy(AutotraderEventLoop, '_drain_signal_inbox', 'signal_inbox')
    steps.spy(LiveTradeExecutor, 'set_current_time', 'clock')
    steps.spy(BookingPeriodRecorder, 'check_boundary', 'boundary')
    steps.spy(AutotraderEventLoop, '_close_sealed_days', 'sealed_days')
    steps.spy(SessionLogRotator, 'check_daily_rotation', 'log_rotation')
    steps.spy(LiveTradeExecutor, 'apply_tick', 'apply_tick')
    steps.spy(LiveTradeExecutor, 'resolve_due', 'resolve_due')
    steps.spy(LiveTradeExecutor, 'heartbeat', 'heartbeat')
    steps.spy(MarketDataEpisodeTracker, 'on_tick', 'tracker')
    steps.spy(AutotraderEventLoop, '_observe_account_value', 'account_value')
    steps.spy(BookingPeriodRecorder, 'observe_tick', 'observe')
    steps.spy(PortfolioManager, 'sample_equity', 'equity')
    steps.spy(BarRenderingController, 'process_tick', 'render')
    steps.spy(SafetyCircuitBreaker, 'check_safety', 'safety')
    steps.spy(SafetyCircuitBreaker, 'entries_blocked', 'entries_blocked')
    steps.spy(DeterministicProbe, 'execute_decision', 'execute')
    steps.spy(AutotraderEventLoop, '_record_rejection', 'record_rejection')
    steps.spy(DecisionEventDispatcher, 'drain', 'drain')
    steps.spy(AutotraderEventLoop, '_reconcile_if_due', 'reconcile')
    steps.spy(AutotraderEventLoop, '_persist_state_if_due', 'persist_state')
    steps.spy(AutotraderEventLoop, '_persist_carry_over_if_needed', 'carry_over')
    steps.spy(AutotraderEventLoop, '_evaluate_market_data_staleness', 'staleness')
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


def _repeats(names: List[str], one_pass: List[str]) -> int:
    """
    How many times a pass repeats at the start of the steps.

    A stop's wait makes as many passes as the venue's answer takes to be heard, so the count is
    the venue's; the order inside each pass is the loop's.

    Args:
        names: The recorded steps
        one_pass: The steps of one pass

    Returns:
        The number of whole repeats
    """
    count = 0
    while names[count * len(one_pass):(count + 1) * len(one_pass)] == one_pass:
        count += 1
    return count


class TestTickPass:

    def test_clock_ticks_bars_account_then_signals_resolutions_drain_algo_execute_drain(
            self, live_steps):
        loop = autotrader_loop(_ticks(2), spot_mode=False, orchestrator=live_steps.orchestrator(),
                               decision_events=True, state_frames=live_steps.frames())

        loop.run()

        assert live_steps.names == (
            _TICK_PASS * 2 + _SESSION_END_EVENT + _SESSION_END_ORDERS)


class TestABacklog:
    """
    #461: live, the ticks that waited on the queue are one slice — the broker path and the bars
    see each, the algo computes once, on the newest, and the workers count the ticks it computed
    for, as a backtest counts only its unclipped ticks. A replay slices by arrival millisecond.
    """

    def test_a_live_backlog_is_one_pass_counted_for_its_newest_tick(self, live_steps):
        ticks = _ticks(3)
        loop = autotrader_loop(ticks, spot_mode=False, orchestrator=live_steps.orchestrator(),
                               decision_events=True, live_source=True)

        loop.run()

        assert live_steps.names.count('apply_tick') == 3
        assert live_steps.algo_ticks == [(ticks[-1], 1)]

    def test_a_replay_computes_on_each_arrival(self, live_steps):
        ticks = _ticks(3)
        loop = autotrader_loop(ticks, spot_mode=False, orchestrator=live_steps.orchestrator(),
                               decision_events=True)

        loop.run()

        assert live_steps.algo_ticks == [(tick, 1) for tick in ticks]


class TestASessionAHookEnds:
    """
    #461: a hook that asks for the session end in the trailing drain leaves the frame and the
    series of its pass, as a backtest's last pass does; one that asks for it in the delivery before
    the algo leaves neither — the algo never ran, and the session ends after that instant.
    """

    @staticmethod
    def _end_at_drain(monkeypatch: pytest.MonkeyPatch, loop: AutotraderEventLoop,
                      which: int) -> None:
        spied = DecisionEventDispatcher.drain
        drains = [0]

        def drain(dispatcher: DecisionEventDispatcher, *args: Any, **kwargs: Any) -> Any:
            result = spied(dispatcher, *args, **kwargs)
            drains[0] += 1
            if drains[0] == which:
                loop._executor.request_session_end('a hook ended the session')
            return result
        monkeypatch.setattr(DecisionEventDispatcher, 'drain', drain)

    def test_an_end_in_the_trailing_drain_keeps_the_passes_records(self, live_steps, monkeypatch):
        loop = autotrader_loop(_ticks(2), spot_mode=False, orchestrator=live_steps.orchestrator(),
                               decision_events=True, state_frames=live_steps.frames(),
                               indicator_series=live_steps.series())
        self._end_at_drain(monkeypatch, loop, which=2)

        loop.run()

        assert live_steps.names[:len(_TICK_PASS) + 1] == _TICK_PASS + ['series']
        assert live_steps.names.count('algo') == 1, 'no second pass'

    def test_an_end_in_the_delivery_before_the_algo_leaves_no_records(self, live_steps,
                                                                      monkeypatch):
        loop = autotrader_loop(_ticks(2), spot_mode=False, orchestrator=live_steps.orchestrator(),
                               decision_events=True, state_frames=live_steps.frames(),
                               indicator_series=live_steps.series())
        self._end_at_drain(monkeypatch, loop, which=1)

        loop.run()

        delivered = _TICK_PASS[:_TICK_PASS.index('drain') + 1]
        assert live_steps.names[:len(delivered)] == delivered
        assert not {'algo', 'frame', 'series'} & set(live_steps.names)

    def test_an_end_in_a_heartbeats_delivery_leaves_no_frame(self, live_steps, monkeypatch):
        """No ghost pass ran, so the timer writes no frame — the cadence's gate is not reached."""
        ticks = _ticks(2)
        after_the_ticks = ticks[-1].timestamp + timedelta(seconds=1)

        class _WallClock(datetime):
            @classmethod
            def now(cls, tz: Optional[Any] = None) -> datetime:
                return after_the_ticks

        monkeypatch.setattr(event_loop_module, 'datetime', _WallClock)
        loop = autotrader_loop(ticks, spot_mode=False,
                               orchestrator=live_steps.orchestrator(
                                   [Decision(action=DecisionLogicAction.FLAT)]),
                               decision_events=True, state_frames=live_steps.frames(),
                               idle_heartbeats=2, heartbeat_interval_ms=5)
        # Two slices drain twice each; the heartbeat's delivery is the fifth
        self._end_at_drain(monkeypatch, loop, which=5)

        loop.run()

        delivered = _IDLE_PASS_QUIET[:_IDLE_PASS_QUIET.index('drain') + 1]
        after_the_slices = live_steps.names[len(_TICK_PASS) * 2:]
        assert after_the_slices[:len(delivered) + 1] == delivered + ['carry_over']
        assert not {'ghost', 'frame'} & set(after_the_slices)


class TestTheIndicatorSeries:
    """#379: the series is recorded right after the frame, and alone it still reads the bars."""

    def test_recorded_right_after_the_frame_on_a_tick_pass(self, live_steps):
        loop = autotrader_loop(_ticks(2), spot_mode=False, orchestrator=live_steps.orchestrator(),
                               decision_events=True, state_frames=live_steps.frames(),
                               indicator_series=live_steps.series())

        loop.run()

        assert live_steps.names == (
            (_TICK_PASS + ['series']) * 2 + _SESSION_END_EVENT + _SESSION_END_ORDERS)

    def test_without_state_frames_the_closed_bars_are_still_read(self, live_steps):
        live_steps.spy(BarRenderingController, 'get_pending_closed_timeframes', 'closed')
        loop = autotrader_loop(_ticks(2), spot_mode=False, orchestrator=live_steps.orchestrator(),
                               decision_events=True, indicator_series=live_steps.series())

        loop.run()

        tick_pass = [step for step in _TICK_PASS if step != 'frame'] + ['series']
        # Read once, after the slice's last tick (#461), as a backtest reads them
        tick_pass.insert(tick_pass.index('signal_inbox'), 'closed')
        assert live_steps.names == tick_pass * 2 + _SESSION_END_EVENT + _SESSION_END_ORDERS


class TestIdlePass:

    def test_clock_inbox_resolutions_staleness_drain_ghost_then_drain(
            self, live_steps, monkeypatch: pytest.MonkeyPatch):
        """Two ticks, then two heartbeats with no tick: the first ghost decision acts."""
        ticks = _ticks(2)
        # The idle passes run on the source's time — the wall clock, which the harness reads
        # from the loop's module — a second after the last tick, so no day ends on them
        after_the_ticks = ticks[-1].timestamp + timedelta(seconds=1)

        class _WallClock(datetime):
            @classmethod
            def now(cls, tz: Optional[Any] = None) -> datetime:
                return after_the_ticks

        monkeypatch.setattr(event_loop_module, 'datetime', _WallClock)
        acting = Decision(action=DecisionLogicAction.FLAT)
        loop = autotrader_loop(ticks, spot_mode=False,
                               orchestrator=live_steps.orchestrator([acting]),
                               decision_events=True, state_frames=live_steps.frames(),
                               idle_heartbeats=2, heartbeat_interval_ms=5)

        loop.run()

        assert live_steps.names == (
            _TICK_PASS * 2 + _IDLE_PASS_ACTING + _IDLE_PASS_QUIET
            + _SESSION_END_EVENT + _SESSION_END_ORDERS)

    def test_a_ghost_decision_executes_as_flat_while_entries_are_blocked(
            self, live_steps, monkeypatch: pytest.MonkeyPatch):
        """The safety layer blocks entries: the ghost pass's BUY reaches the logic as FLAT."""
        ticks = _ticks(2)
        after_the_ticks = ticks[-1].timestamp + timedelta(seconds=1)

        class _WallClock(datetime):
            @classmethod
            def now(cls, tz: Optional[Any] = None) -> datetime:
                return after_the_ticks

        monkeypatch.setattr(event_loop_module, 'datetime', _WallClock)
        monkeypatch.setattr(SafetyCircuitBreaker, 'entries_blocked', lambda breaker: True)
        ghost_actions: List[DecisionLogicAction] = []
        spied_execute = DeterministicProbe.execute_decision

        def execute(logic: DeterministicProbe, decision: Decision,
                    tick: Optional[TickData] = None) -> Any:
            if tick is None:
                ghost_actions.append(decision.action)
            return spied_execute(logic, decision, tick)

        monkeypatch.setattr(DeterministicProbe, 'execute_decision', execute)
        loop = autotrader_loop(ticks, spot_mode=False,
                               orchestrator=live_steps.orchestrator(
                                   [Decision(action=DecisionLogicAction.BUY)]),
                               decision_events=True, idle_heartbeats=1, heartbeat_interval_ms=5)

        loop.run()

        assert ghost_actions == [DecisionLogicAction.FLAT]


class TestOrderWaitPass:
    """
    A stop asked for before the first pass, with a market buy on its way: the loop makes no tick
    or idle pass and goes straight to the wait, as a SIGTERM during the start does.
    """

    @pytest.fixture
    def executor(self, live_steps: PassSteps):
        """
        An executor that has seen one tick, and stops its request worker afterwards.

        Args:
            live_steps: The step recorder; the tick fed here is the fixture's, not the loop's

        Yields:
            The executor
        """
        executor = live_executor(spot_mode=False)
        MockOrderExecution().feed_tick(executor, timestamp=_ticks(1)[0].timestamp)
        live_steps.names.clear()
        yield executor
        executor.get_request_processor().stop_worker(0.0)

    @staticmethod
    def _buy(executor: LiveTradeExecutor) -> None:
        """
        Send one market buy.

        Args:
            executor: The executor to send it through
        """
        executor.open_order(OpenOrderRequest(
            symbol=SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=LOTS))

    def test_clock_boundary_heartbeat_drain_carry_over(self, live_steps, executor):
        self._buy(executor)
        executor.request_session_end('stop asked')
        loop = autotrader_loop([], spot_mode=False, executor=executor,
                               orchestrator=live_steps.orchestrator(), decision_events=True,
                               state_frames=live_steps.frames(), heartbeat_interval_ms=5)

        loop.run()

        passes = _repeats(live_steps.names, _WAIT_PASS_HEARD)
        assert passes >= 1, live_steps.names
        assert live_steps.names == (
            _WAIT_PASS_HEARD * passes + _SESSION_END_EVENT + _SESSION_END_ORDERS)

    def test_after_the_session_end_event_nothing_is_heard(
            self, live_steps, executor, monkeypatch: pytest.MonkeyPatch):
        """The logic answers the session-end event with an order; the second wait settles it."""

        def subscribed(cls: type) -> Set[DecisionEventType]:
            return {DecisionEventType.SESSION_END}

        monkeypatch.setattr(DeterministicProbe, 'get_subscribed_events', classmethod(subscribed))
        monkeypatch.setattr(DeterministicProbe, 'on_session_end',
                            lambda logic, event: self._buy(executor))
        executor.request_session_end('stop asked')
        loop = autotrader_loop([], spot_mode=False, executor=executor,
                               orchestrator=live_steps.orchestrator(), decision_events=True,
                               state_frames=live_steps.frames(), heartbeat_interval_ms=5)

        loop.run()

        # Nothing was on its way before the event, so the first wait made no pass
        names = live_steps.names
        assert names[:len(_SESSION_END_EVENT)] == _SESSION_END_EVENT
        after_event = names[len(_SESSION_END_EVENT):]
        passes = _repeats(after_event, _WAIT_PASS_UNHEARD)
        assert passes >= 1, names
        assert after_event == _WAIT_PASS_UNHEARD * passes + _SESSION_END_ORDERS
