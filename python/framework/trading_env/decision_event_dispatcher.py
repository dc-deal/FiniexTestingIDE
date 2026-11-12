"""
FiniexTestingIDE - Decision Event Dispatcher (#348)

Bridges the executor's event sources to the decision logic's typed event hooks.

Order fills, rejections and unaccounted orders ride the executor's existing
order-outcome listener fan-out (#319). Cancellations, partial closes, and session-end arrive via the
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
from python.framework.trading_env.algo_masks.algo_order_result import AlgoOrderResult
from python.framework.types.decision_event_types import (
    DecisionEvent,
    DecisionEventType,
    OrderCancelledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    OrderUnaccountedEvent,
    PartialCloseEvent,
    PositionClosedEvent,
    SessionEndEvent,
    SessionEndOrderOutcomeEvent,
    SessionEndSeverity,
)
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderResult,
    OrderStatus,
)

# Payload class → event type, for subscription filtering in submit().
_EVENT_TYPE_BY_CLASS: Dict[Type[DecisionEvent], DecisionEventType] = {
    OrderFilledEvent: DecisionEventType.ORDER_FILLED,
    OrderRejectedEvent: DecisionEventType.ORDER_REJECTED,
    OrderUnaccountedEvent: DecisionEventType.ORDER_UNACCOUNTED,
    OrderCancelledEvent: DecisionEventType.ORDER_CANCELLED,
    PartialCloseEvent: DecisionEventType.PARTIAL_CLOSE,
    PositionClosedEvent: DecisionEventType.POSITION_CLOSED,
    SessionEndEvent: DecisionEventType.SESSION_END,
    SessionEndOrderOutcomeEvent: DecisionEventType.SESSION_END_ORDER_OUTCOME,
}

# The events that report how one order ended — after the session-end event, each is reported
# as that order's session-end outcome instead (#642)
_ORDER_ENDINGS = (
    OrderFilledEvent,
    OrderRejectedEvent,
    OrderUnaccountedEvent,
    OrderCancelledEvent,
    PartialCloseEvent,
    PositionClosedEvent,
)


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
        # Set once the session-end event is out: what an order does after it is reported as
        # that order's session-end outcome
        self._session_ended = False

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

        Once the session-end event is out, the ending of an order the strategy sent from its
        session-end hooks arrives as that order's session-end outcome. The ending of an order sent
        before — a resting order the cleanup cancels, one a live venue fills meanwhile — is not
        delivered, as it never was after the session-end event.

        Args:
            event: The decision event to buffer
        """
        if self._session_ended and isinstance(event, _ORDER_ENDINGS):
            result = event.result
            if not self._executor.was_sent_after_session_end(result.order_id):
                return
            event = SessionEndOrderOutcomeEvent(
                order_id=result.order_id,
                # A close is keyed by its position's id, and an ending row of one carries none
                position_id=(result.position_id if result.position_id is not None
                             or result.action is not OrderAction.CLOSE else result.order_id),
                status=result.status,
                # Already the mask the ending event carried
                result=result,
                tick_time=event.tick_time,
            )
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

    def emit_session_end(self) -> None:
        """
        Tell the decision logic why the session ended, once its loop is over (#348).

        Both pipelines end the same way, so the event is built here rather than in each loop:
        the reason and severity of the request when one was made, otherwise the input ran out.
        Delivered at once, before teardown and reporting. The executor starts recording what is
        sent from here on, so the orders the strategy sends from the event can be settled and
        reported apart from those already on their way (#642).
        """
        self._executor.begin_session_end()
        if self._executor.is_session_end_requested():
            end_reason = self._executor.get_session_end_reason()
            end_severity = self._executor.get_session_end_severity()
        else:
            end_reason = 'tick source exhausted'
            end_severity = SessionEndSeverity.NORMAL
        self.submit(SessionEndEvent(
            reason=end_reason,
            severity=end_severity,
            tick_time=self._executor.get_current_time(),
        ))
        self._session_ended = True
        self.drain()

    def emit_session_end_orders(self) -> None:
        """
        Tell the decision logic how the orders it sent from on_session_end ended (#642).

        Called by both pipelines once those orders are settled — the simulation's settling step,
        a live stop's second wait — and before the cleanup. The last delivery of a session: an
        order sent from this hook is withdrawn, never sent.
        """
        self.drain()

    def _on_order_outcome(
        self,
        direction: OrderDirection,
        result: OrderResult,
        pending: Optional[PendingOrder] = None,
    ) -> None:
        """
        Bridge the executor's order-outcome fan-out to ORDER_FILLED / ORDER_REJECTED / ORDER_UNACCOUNTED.

        Open fills arrive as EXECUTED; venue refusals as REJECTED, and an order the venue never
        received as UNDELIVERED — both are ORDER_REJECTED, and the event's result carries which;
        an order nobody could account for as UNACCOUNTED. Close fills do not reach the outcome
        fan-out (see _fill_close_order) — partial closes arrive via the sink instead.

        The event's direction is the row's wherever the row has one. A close in the live
        pipeline carries no direction of its own, so the fan-out hands over None for it,
        while its row states the direction of the position it closes — None only once that
        position is gone.

        Args:
            direction: Position direction, as the fan-out has it
            result: Terminal OrderResult
            pending: PendingOrder reference at outcome time (unused here)
        """
        direction = result.direction or direction
        tick_time = self._executor.get_current_time()
        if result.status == OrderStatus.EXECUTED:
            self.submit(OrderFilledEvent(
                order_id=result.order_id,
                position_id=result.position_id,
                direction=direction,
                fill_price=result.executed_price,
                lots=result.executed_lots,
                result=AlgoOrderResult(result),
                tick_time=tick_time,
            ))
        elif result.status in (OrderStatus.REJECTED, OrderStatus.UNDELIVERED):
            self.submit(OrderRejectedEvent(
                order_id=result.order_id,
                direction=direction,
                reason=result.rejection_reason,
                message=result.rejection_message,
                result=AlgoOrderResult(result),
                tick_time=tick_time,
            ))
        elif result.status is OrderStatus.UNACCOUNTED:
            self.submit(OrderUnaccountedEvent(
                order_id=result.order_id,
                direction=direction,
                end_reason=result.end_reason,
                result=AlgoOrderResult(result),
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
        elif isinstance(event, OrderUnaccountedEvent):
            self._decision_logic.on_order_unaccounted(event)
        elif isinstance(event, OrderCancelledEvent):
            self._decision_logic.on_order_cancelled(event)
        elif isinstance(event, PartialCloseEvent):
            self._decision_logic.on_partial_close(event)
        elif isinstance(event, PositionClosedEvent):
            self._decision_logic.on_position_closed(event)
        elif isinstance(event, SessionEndEvent):
            self._decision_logic.on_session_end(event)
        elif isinstance(event, SessionEndOrderOutcomeEvent):
            self._decision_logic.on_session_end_order_outcome(event)
