"""
FiniexTestingIDE - AutoTrader Pass Order Tests

What the AutoTrader tick loop calls in each kind of pass, and in which order: the tick pass, the
idle pass when no tick came within the heartbeat, and the pass a stop's wait makes while orders are
still on their way.

Pinned before the passes move behind one dispatcher (#458), which must not change behaviour. These
tests were written against the loop before the move and stay unchanged through it, so a step that
moves, disappears or arrives twice shows here first. When the passes take the phase order of the
event model (#461), the differences become deliberate and are written down here.

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

from python.framework.autotrader import autotrader_tick_loop as tick_loop_module
from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
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

# A tick: the signal inbox is merged BEFORE the clock moves, the day boundary is checked and its
# sealed days closed, the broker path sees the tick, the account is valued and sampled, the bars
# render and the algo computes; the safety sample, the FLAT override's question and the execute
# follow, then one drain. The account is sampled twice, once with its value and once for safety.
# Reconcile, persistence and 'frame' already belong to what the loop does after the pass.
_TICK_PASS = ['signal_inbox', 'clock', 'boundary', 'sealed_days', 'log_rotation', 'on_tick',
              'tracker', 'account_value', 'observe', 'equity', 'render', 'algo', 'equity',
              'safety', 'entries_blocked', 'execute', 'record_rejection', 'drain', 'reconcile',
              'persist_state', 'carry_over', 'frame']
# No tick within the heartbeat: the clock moves without one, the venue is heard, staleness and the
# reconcile come before the inbox and the drain, and the ghost pass last — with no drain after it
_IDLE_PASS_ACTING = ['clock', 'boundary', 'sealed_days', 'log_rotation', 'heartbeat', 'staleness',
                     'reconcile', 'signal_inbox', 'drain', 'ghost', 'entries_blocked', 'execute',
                     'record_rejection', 'persist_state', 'carry_over', 'frame']
_IDLE_PASS_QUIET = ['clock', 'boundary', 'sealed_days', 'log_rotation', 'heartbeat', 'staleness',
                    'reconcile', 'signal_inbox', 'drain', 'ghost', 'persist_state', 'carry_over',
                    'frame']
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
    steps.spy(AutotraderTickLoop, '_drain_signal_inbox', 'signal_inbox')
    steps.spy(LiveTradeExecutor, 'set_current_time', 'clock')
    steps.spy(BookingPeriodRecorder, 'check_boundary', 'boundary')
    steps.spy(AutotraderTickLoop, '_close_sealed_days', 'sealed_days')
    steps.spy(SessionLogRotator, 'check_daily_rotation', 'log_rotation')
    steps.spy(LiveTradeExecutor, 'on_tick', 'on_tick')
    steps.spy(LiveTradeExecutor, 'heartbeat', 'heartbeat')
    steps.spy(MarketDataEpisodeTracker, 'on_tick', 'tracker')
    steps.spy(AutotraderTickLoop, '_observe_account_value', 'account_value')
    steps.spy(BookingPeriodRecorder, 'observe_tick', 'observe')
    steps.spy(PortfolioManager, 'sample_equity', 'equity')
    steps.spy(BarRenderingController, 'process_tick', 'render')
    steps.spy(SafetyCircuitBreaker, 'check_safety', 'safety')
    steps.spy(SafetyCircuitBreaker, 'entries_blocked', 'entries_blocked')
    steps.spy(DeterministicProbe, 'execute_decision', 'execute')
    steps.spy(AutotraderTickLoop, '_record_rejection', 'record_rejection')
    steps.spy(DecisionEventDispatcher, 'drain', 'drain')
    steps.spy(AutotraderTickLoop, '_reconcile_if_due', 'reconcile')
    steps.spy(AutotraderTickLoop, '_persist_state_if_due', 'persist_state')
    steps.spy(AutotraderTickLoop, '_persist_carry_over_if_needed', 'carry_over')
    steps.spy(AutotraderTickLoop, '_evaluate_market_data_staleness', 'staleness')
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

    def test_inbox_before_the_clock_then_broker_account_bars_algo_safety_execute_drain(
            self, live_steps):
        loop = autotrader_loop(_ticks(2), spot_mode=False, orchestrator=live_steps.orchestrator(),
                               decision_events=True, state_frames=live_steps.frames())

        loop.run()

        assert live_steps.names == (
            _TICK_PASS * 2 + _SESSION_END_EVENT + _SESSION_END_ORDERS)


class TestIdlePass:

    def test_clock_boundary_heartbeat_staleness_reconcile_inbox_drain_then_ghost(
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

        monkeypatch.setattr(tick_loop_module, 'datetime', _WallClock)
        acting = Decision(action=DecisionLogicAction.FLAT)
        loop = autotrader_loop(ticks, spot_mode=False,
                               orchestrator=live_steps.orchestrator([acting]),
                               decision_events=True, state_frames=live_steps.frames(),
                               idle_heartbeats=2, heartbeat_interval_ms=5)

        loop.run()

        assert live_steps.names == (
            _TICK_PASS * 2 + _IDLE_PASS_ACTING + _IDLE_PASS_QUIET
            + _SESSION_END_EVENT + _SESSION_END_ORDERS)


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
