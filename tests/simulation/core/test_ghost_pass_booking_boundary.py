"""
The trading-day boundary at every instant of the backtest's timeline (#537 / #539 audit, #460).

A quiet stretch between two data ticks is crossed by the instants the timeline puts there — a
heartbeat logic's timers, an order coming due — and they are the only thing that advances the
canonical clock there. So the boundary is checked at each instant, at its own stamp — checked
once before them it would run on the clock the PREVIOUS tick already checked, which is no check
at all, and a fill resolved after a rollover would then be booked into the day before it. Until
#460 a gap walk made the heartbeat instants; the loop core's instant entry now asks at every one.

These pin the call, not the seal: whether a day flip seals is `BookingPeriodRecorder`'s own
question and is tested there. What can only be seen here is WHEN the loop asks.
"""

from datetime import datetime, timezone
from typing import List
from unittest.mock import MagicMock

from python.framework.loop.loop_context import LoopContext
from python.framework.loop.loop_event_dispatcher import dispatch_backtest_instant
from python.framework.types.loop_types.backtest_pass_kind_types import BacktestPassKind
from python.framework.types.loop_types.loop_event_types import ResolutionEvent, TimeEvent
from python.framework.types.loop_types.loop_pass_record_types import LoopPassRecord

_START_MSC = 1_758_000_000_000    # an arbitrary instant; only the deltas matter


def _simulator(order: List[str]) -> MagicMock:
    """
    A simulator whose clock moves with the instant it is given, recording its resolution calls.

    Args:
        order: Where the calls are recorded

    Returns:
        The simulator double
    """
    simulator = MagicMock()
    state = {}

    def _begin(stamp: int) -> None:
        state['now'] = datetime.fromtimestamp(stamp / 1000.0, tz=timezone.utc)

    simulator.begin_instant.side_effect = _begin
    simulator.get_current_time.side_effect = lambda: state.get('now')
    simulator.is_session_end_requested.return_value = False
    simulator.resolve_scheduled.side_effect = lambda stamp: order.append('scheduled')
    simulator.resolve_due.side_effect = lambda stamp: order.append('due')
    return simulator


def _run(events, order: List[str], booking: MagicMock) -> BacktestPassKind:
    """
    Run one instant through the loop core's backtest entry.

    Args:
        events: The instant's events
        order: Where the simulator's resolution calls are recorded
        booking: The booking recorder double

    Returns:
        What the instant did with the algo
    """
    simulator = _simulator(order)
    booking.check_boundary.side_effect = lambda *_: order.append('boundary')
    ctx = LoopContext(
        executor=simulator, bar_controller=MagicMock(), orchestrator=MagicMock(),
        decision_logic=MagicMock(), decision_event_dispatcher=None, symbol='BTCUSD',
        booking=booking, seal_source=lambda: ([], MagicMock()), reads_closed_bars=False,
        profiling_enabled=False, profile_times={}, profile_counts={})
    return dispatch_backtest_instant(events, simulator, ctx, MagicMock(), LoopPassRecord())


class TestTheBoundaryIsAskedAtEveryInstant:

    def test_a_timer_instant_checks_at_its_own_stamp(self):
        booking = MagicMock()
        stamp = _START_MSC + 2000
        kind = _run((TimeEvent(stamp, stamp, 0),), [], booking)
        assert kind is BacktestPassKind.GHOST_PASS
        booking.check_boundary.assert_called_once()
        assert booking.check_boundary.call_args.args[0] == datetime.fromtimestamp(
            stamp / 1000.0, tz=timezone.utc)

    def test_an_instant_with_only_a_due_time_checks_too(self):
        booking = MagicMock()
        stamp = _START_MSC + 3250
        kind = _run((ResolutionEvent(stamp, stamp, 0),), [], booking)
        assert kind is BacktestPassKind.NO_PASS
        booking.check_boundary.assert_called_once()

    def test_the_check_precedes_the_resolutions_of_its_instant(self):
        """
        The boundary is the CUT: what closed earlier belongs to the day that is ending, and a
        fill resolving AT this instant belongs to the day that is opening — the period window
        is end-exclusive, so the order of these calls decides which day a fill lands in.
        """
        order: List[str] = []
        stamp = _START_MSC + 1000
        _run((ResolutionEvent(stamp, stamp, 0),), order, MagicMock())
        assert order == ['boundary', 'scheduled', 'due']
