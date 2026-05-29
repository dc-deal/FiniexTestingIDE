"""
FiniexTestingIDE - DecisionEventDispatcher Tests (#348)

Validates the decision event channel mechanics in isolation:
- create_if_subscribed returns None when the logic subscribes to nothing
- Order outcomes map to ORDER_FILLED / ORDER_REJECTED
- Unsubscribed event types are filtered out
- drain() delivers buffered events to the hooks in FIFO order
- drain() is drain-to-completion + re-entrancy safe (an event emitted from
  inside a hook lands in the NEXT drain, not the current one)
"""

from datetime import datetime, timezone
from typing import Callable, List, Optional, Set

from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.types.decision_event_types import (
    DecisionEventType,
    OrderCancelledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    PartialCloseEvent,
    SessionEndEvent,
    SessionEndSeverity,
)
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderResult,
    OrderStatus,
    RejectionReason,
)


# =============================================================================
# Harness
# =============================================================================


class _FakeExecutor:
    """
    Minimal executor stub exposing only what the dispatcher wires to:
    the order-outcome listener registration, the decision-event sink, and
    the clock. No worker thread, no portfolio.
    """

    def __init__(self) -> None:
        self.outcome_listeners: List[Callable] = []
        self.decision_event_sink: Optional[Callable] = None

    def add_order_outcome_listener(self, listener: Callable) -> None:
        self.outcome_listeners.append(listener)

    def set_decision_event_sink(self, sink: Callable) -> None:
        self.decision_event_sink = sink

    def get_current_time(self) -> datetime:
        return datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)

    def fire_outcome(self, direction: OrderDirection, result: OrderResult) -> None:
        """Simulate the executor's _notify_outcome fan-out."""
        for listener in self.outcome_listeners:
            listener(direction, result, None)


class _RecordingLogic:
    """Records every hook invocation for assertions."""

    def __init__(self) -> None:
        self.filled: List[OrderFilledEvent] = []
        self.rejected: List[OrderRejectedEvent] = []
        self.cancelled: List[OrderCancelledEvent] = []
        self.partial_closes: List[PartialCloseEvent] = []
        self.session_ends: List[SessionEndEvent] = []
        self.order: List[str] = []

    def on_order_filled(self, event: OrderFilledEvent) -> None:
        self.filled.append(event)
        self.order.append(f'filled:{event.order_id}')

    def on_order_rejected(self, event: OrderRejectedEvent) -> None:
        self.rejected.append(event)
        self.order.append(f'rejected:{event.order_id}')

    def on_order_cancelled(self, event: OrderCancelledEvent) -> None:
        self.cancelled.append(event)
        self.order.append(f'cancelled:{event.order_id}')

    def on_partial_close(self, event: PartialCloseEvent) -> None:
        self.partial_closes.append(event)
        self.order.append(f'partial:{event.position_id}')

    def on_session_end(self, event: SessionEndEvent) -> None:
        self.session_ends.append(event)
        self.order.append(f'session_end:{event.reason}')


def _make_logic(subscribed: Set[DecisionEventType]) -> _RecordingLogic:
    """Build a recording logic whose get_subscribed_events() returns the given set."""

    class _Logic(_RecordingLogic):
        @classmethod
        def get_subscribed_events(cls) -> Set[DecisionEventType]:
            return subscribed

    return _Logic()


def _executed_result(order_id: str = 'pos_1') -> OrderResult:
    return OrderResult(
        order_id=order_id,
        status=OrderStatus.EXECUTED,
        executed_price=100.0,
        executed_lots=0.1,
        position_id=order_id,
    )


def _rejected_result(order_id: str = 'pos_1') -> OrderResult:
    return OrderResult(
        order_id=order_id,
        status=OrderStatus.REJECTED,
        rejection_reason=RejectionReason.INSUFFICIENT_MARGIN,
        rejection_message='no margin',
    )


def _partial_close_event(position_id: str = 'pos_1') -> PartialCloseEvent:
    return PartialCloseEvent(
        position_id=position_id,
        direction=OrderDirection.LONG,
        closed_lots=0.05,
        remaining_lots=0.05,
        fill_price=101.0,
        result=_executed_result(position_id),
    )


_ALL = {
    DecisionEventType.ORDER_FILLED,
    DecisionEventType.ORDER_REJECTED,
    DecisionEventType.ORDER_CANCELLED,
    DecisionEventType.PARTIAL_CLOSE,
    DecisionEventType.SESSION_END,
}


# =============================================================================
# Tests
# =============================================================================


def test_create_if_subscribed_returns_none_when_empty():
    logic = _make_logic(set())
    executor = _FakeExecutor()
    dispatcher = DecisionEventDispatcher.create_if_subscribed(
        logic, executor, GlobalLogger())
    assert dispatcher is None
    # No wiring happened — zero overhead.
    assert executor.outcome_listeners == []
    assert executor.decision_event_sink is None


def test_create_if_subscribed_wires_executor():
    logic = _make_logic({DecisionEventType.ORDER_FILLED})
    executor = _FakeExecutor()
    dispatcher = DecisionEventDispatcher.create_if_subscribed(
        logic, executor, GlobalLogger())
    assert dispatcher is not None
    assert len(executor.outcome_listeners) == 1
    # Bound methods compare equal (same instance + func) but are not `is`-identical
    # (a fresh bound-method object is created on each attribute access).
    assert executor.decision_event_sink == dispatcher.submit


def test_executed_outcome_maps_to_order_filled():
    logic = _make_logic(_ALL)
    executor = _FakeExecutor()
    dispatcher = DecisionEventDispatcher.create_if_subscribed(
        logic, executor, GlobalLogger())

    executor.fire_outcome(OrderDirection.LONG, _executed_result('pos_7'))
    assert logic.filled == []  # buffered, not delivered yet

    dispatcher.drain()
    assert len(logic.filled) == 1
    assert logic.filled[0].order_id == 'pos_7'
    assert logic.filled[0].fill_price == 100.0
    assert logic.filled[0].lots == 0.1
    assert logic.filled[0].direction == OrderDirection.LONG


def test_rejected_outcome_maps_to_order_rejected():
    logic = _make_logic(_ALL)
    executor = _FakeExecutor()
    dispatcher = DecisionEventDispatcher.create_if_subscribed(
        logic, executor, GlobalLogger())

    executor.fire_outcome(OrderDirection.SHORT, _rejected_result('pos_9'))
    dispatcher.drain()
    assert len(logic.rejected) == 1
    assert logic.rejected[0].order_id == 'pos_9'
    assert logic.rejected[0].reason == RejectionReason.INSUFFICIENT_MARGIN


def test_unsubscribed_event_is_filtered():
    # Subscribes to fills only — a partial close must be dropped.
    logic = _make_logic({DecisionEventType.ORDER_FILLED})
    executor = _FakeExecutor()
    dispatcher = DecisionEventDispatcher.create_if_subscribed(
        logic, executor, GlobalLogger())

    dispatcher.submit(_partial_close_event())
    dispatcher.drain()
    assert logic.partial_closes == []


def test_fifo_ordering_across_sources():
    logic = _make_logic(_ALL)
    executor = _FakeExecutor()
    dispatcher = DecisionEventDispatcher.create_if_subscribed(
        logic, executor, GlobalLogger())

    executor.fire_outcome(OrderDirection.LONG, _executed_result('a'))
    dispatcher.submit(_partial_close_event('a'))
    dispatcher.submit(SessionEndEvent(reason='done', severity=SessionEndSeverity.NORMAL))
    dispatcher.drain()
    assert logic.order == ['filled:a', 'partial:a', 'session_end:done']


def test_drain_is_reentrancy_safe():
    # A hook that emits another event must not have it delivered in the same
    # drain — it lands in the next drain.
    logic = _make_logic(_ALL)
    executor = _FakeExecutor()
    dispatcher = DecisionEventDispatcher.create_if_subscribed(
        logic, executor, GlobalLogger())

    def _on_filled(event: OrderFilledEvent) -> None:
        _RecordingLogic.on_order_filled(logic, event)
        dispatcher.submit(_partial_close_event(event.order_id))

    logic.on_order_filled = _on_filled  # type: ignore[method-assign]

    executor.fire_outcome(OrderDirection.LONG, _executed_result('z'))
    dispatcher.drain()
    assert len(logic.filled) == 1
    assert logic.partial_closes == []  # emitted during drain → deferred

    dispatcher.drain()
    assert len(logic.partial_closes) == 1


def test_drain_empty_buffer_is_noop():
    logic = _make_logic(_ALL)
    executor = _FakeExecutor()
    dispatcher = DecisionEventDispatcher.create_if_subscribed(
        logic, executor, GlobalLogger())
    dispatcher.drain()
    assert logic.order == []
