"""
FiniexTestingIDE - Decision Event Dispatcher (#348)

Bridges the executor's event sources to the decision logic's typed event hooks.

Order fills and rejections ride the executor's existing order-outcome listener
fan-out (#319). Cancellations, partial closes, and session-end arrive via the
executor's dedicated decision-event sink. The dispatcher buffers every event and
the tick loop drains it at the loop boundary, so events are processed in order —
after the tick's compute/execute and before the next tick.

Drain ordering & re-entrancy: submit() only buffers; hooks fire exclusively in
drain(). drain() takes the buffer and clears it before invoking hooks, so an
order submitted from inside a hook resolves later and its event lands in the next
drain — no re-entrant cascades.
"""

from typing import Dict, List, Optional, Set, Type

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.decision_event_types import (
    DecisionEvent,
    DecisionEventType,
    OrderCancelledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    PartialCloseEvent,
    SessionEndEvent,
)
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderResult,
    OrderStatus,
)


# Payload class → event type, for subscription filtering in submit().
_EVENT_TYPE_BY_CLASS: Dict[Type[DecisionEvent], DecisionEventType] = {
    OrderFilledEvent: DecisionEventType.ORDER_FILLED,
    OrderRejectedEvent: DecisionEventType.ORDER_REJECTED,
    OrderCancelledEvent: DecisionEventType.ORDER_CANCELLED,
    PartialCloseEvent: DecisionEventType.PARTIAL_CLOSE,
    SessionEndEvent: DecisionEventType.SESSION_END,
}


class DecisionEventDispatcher:
    """
    Buffers decision events and delivers them to the decision logic's hooks
    at the tick-loop boundary.

    Constructed at startup only when the active decision logic subscribes to at
    least one event (zero overhead otherwise). Registers on the executor's
    order-outcome fan-out and sets itself as the executor's decision-event sink.
    """

    def __init__(
        self,
        decision_logic: AbstractDecisionLogic,
        executor: AbstractTradeExecutor,
        logger: AbstractLogger,
    ):
        """
        Initialize and wire the dispatcher to the executor.

        Args:
            decision_logic: The decision logic whose hooks receive events
            executor: The trade executor producing the events
            logger: Logger for diagnostics
        """
        self._decision_logic = decision_logic
        self._executor = executor
        self._logger = logger
        self._subscribed: Set[DecisionEventType] = set(
            type(decision_logic).get_subscribed_events()
        )
        self._buffer: List[DecisionEvent] = []

        executor.add_order_outcome_listener(self._on_order_outcome)
        executor.set_decision_event_sink(self.submit)

    @classmethod
    def create_if_subscribed(
        cls,
        decision_logic: AbstractDecisionLogic,
        executor: AbstractTradeExecutor,
        logger: AbstractLogger,
    ) -> Optional['DecisionEventDispatcher']:
        """
        Build a dispatcher only if the decision logic subscribes to events.

        Returns None when get_subscribed_events() is empty, so the runner can
        skip draining entirely — zero overhead for non-subscribing logics.

        Args:
            decision_logic: The active decision logic
            executor: The trade executor
            logger: Logger for diagnostics

        Returns:
            A wired DecisionEventDispatcher, or None if no events are subscribed
        """
        if not type(decision_logic).get_subscribed_events():
            return None
        return cls(decision_logic, executor, logger)

    def submit(self, event: DecisionEvent) -> None:
        """
        Buffer an event if its type is subscribed.

        Called by the executor's decision-event sink (cancel / partial close /
        session end) and internally from the order-outcome bridge. Buffering
        only — hooks fire in drain().

        Args:
            event: The decision event to buffer
        """
        event_type = _EVENT_TYPE_BY_CLASS[type(event)]
        if event_type not in self._subscribed:
            return
        self._buffer.append(event)

    def drain(self) -> None:
        """
        Deliver all buffered events to the decision logic hooks in FIFO order.

        Swaps the buffer before dispatching, so events emitted from inside a
        hook are processed on the next drain (re-entrancy guard).
        """
        if not self._buffer:
            return
        pending = self._buffer
        self._buffer = []
        for event in pending:
            self._dispatch(event)

    def _on_order_outcome(
        self,
        direction: OrderDirection,
        result: OrderResult,
        pending: Optional[PendingOrder] = None,
    ) -> None:
        """
        Bridge the executor's order-outcome fan-out to ORDER_FILLED / ORDER_REJECTED.

        Open fills arrive as EXECUTED, rejections as REJECTED. Close fills do not
        reach the outcome fan-out (see _fill_close_order) — partial closes arrive
        via the sink instead.

        Args:
            direction: Position direction
            result: Terminal OrderResult
            pending: PendingOrder reference at outcome time (unused here)
        """
        tick_time = self._executor.get_current_time()
        if result.status == OrderStatus.EXECUTED:
            self.submit(OrderFilledEvent(
                order_id=result.order_id,
                position_id=result.position_id,
                direction=direction,
                fill_price=result.executed_price,
                lots=result.executed_lots,
                result=result,
                tick_time=tick_time,
            ))
        elif result.is_rejected:
            self.submit(OrderRejectedEvent(
                order_id=result.order_id,
                direction=direction,
                reason=result.rejection_reason,
                message=result.rejection_message,
                result=result,
                tick_time=tick_time,
            ))

    def _dispatch(self, event: DecisionEvent) -> None:
        """
        Route one event to the matching decision-logic hook.

        Args:
            event: The buffered decision event
        """
        if isinstance(event, OrderFilledEvent):
            self._decision_logic.on_order_filled(event)
        elif isinstance(event, OrderRejectedEvent):
            self._decision_logic.on_order_rejected(event)
        elif isinstance(event, OrderCancelledEvent):
            self._decision_logic.on_order_cancelled(event)
        elif isinstance(event, PartialCloseEvent):
            self._decision_logic.on_partial_close(event)
        elif isinstance(event, SessionEndEvent):
            self._decision_logic.on_session_end(event)
