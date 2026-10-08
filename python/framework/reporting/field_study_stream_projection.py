"""
FiniexTestingIDE - Field Study Stream Projection (#566)

The field study's order and venue lines, copied from the live core's own record while it is written.
Every order transition and every venue read of the session reaches this listener at the moment the
executor records it, and the line written here is that record in the field study's schema. The study
keeps no picture of its orders beside the session's any more — it reads the one the session keeps.
What the study owns is its choreography, phases and their outcomes, which the recorder writes; both
go through the recorder's one writer, so the capture keeps one sequence.

A line names the phase that SUBMITTED its order, not the phase current when the answer arrived: the
phase is taken when the submission is recorded and travels with every later event of it, keyed by
the submission because a position's open and its closes share one order id.
"""

from typing import Any, Dict, Optional, Tuple

from python.framework.reporting.field_study_recorder import (
    PLANE_BOT,
    PLANE_BROKER_TRUTH,
    PREFLIGHT_PHASE,
    SESSION_END_PHASE,
    FieldStudyRecorder,
)
from python.framework.reporting.io.order_event_stream_io import broker_truth_row
from python.framework.types.live_types.broker_truth_types import (
    BrokerTruthReadReason,
    BrokerTruthRecord,
)
from python.framework.types.trading_env_types.order_event_types import OrderEvent, OrderEventType
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderSide,
    OrderType,
    direction_to_side,
)

# A fill is written as an open, a partial close or a full close — decided per event, from the lots
# its position still holds.
FILL_LINE = 'fill'

# Which line each order event becomes. None writes no line, and the reason stands beside it; a test
# holds this map to the enum, so a new event type cannot arrive here unwritten by accident.
FIELD_STUDY_LINE_BY_EVENT: Dict[OrderEventType, Optional[str]] = {
    OrderEventType.SUBMITTED: None,          # kept for its phase and mid; the outcome is the line
    OrderEventType.ACCEPTED: None,           # the outcome that follows is the line
    OrderEventType.REJECTED: 'order_rejected',
    OrderEventType.DENIED: 'order_rejected',  # refused here before anything was sent
    OrderEventType.UNRESOLVED: None,         # a question; the outcome that settles it is the line
    OrderEventType.RESOLVED: None,
    OrderEventType.TRIGGERED: None,          # the simulation's alone; a live stop shows as its fill
    OrderEventType.MODIFY_REQUESTED: None,   # a modify is judged by the order it leaves resting
    OrderEventType.MODIFIED: None,
    OrderEventType.MODIFY_REJECTED: None,
    OrderEventType.CANCEL_REQUESTED: None,   # the ending is the line
    OrderEventType.CANCEL_DEFERRED: None,
    OrderEventType.CANCELLED: 'order_cancelled',
    OrderEventType.CANCEL_REJECTED: None,    # the order goes on; whatever ends it is the line
    OrderEventType.PARTIALLY_FILLED: FILL_LINE,
    OrderEventType.FILLED: FILL_LINE,
    OrderEventType.EXPIRED: 'order_cancelled',     # its status says expired
    OrderEventType.UNDELIVERED: 'order_rejected',  # its status says undelivered
    OrderEventType.UNACCOUNTED: 'order_unaccounted',
    OrderEventType.ADOPTED: None,            # a field study starts flat; the preflight sees to it
}

# Below this a position's remaining lots count as gone — a full close, not a partial one.
_LOTS_EPSILON = 1e-9


class FieldStudyStreamProjection:
    """
    The executor's order-event and broker-truth listener for a Field Study session.

    Args:
        recorder: The study's recorder — its writer, its sequence and its current phase
        unit_name: The session's unit, which a broker-truth row is filed under
    """

    def __init__(self, recorder: FieldStudyRecorder, unit_name: str):
        self._recorder = recorder
        self._unit_name = unit_name
        # Per submission: the phase that sent it, and what its fill is measured against
        self._phase_of: Dict[int, Tuple[str, int]] = {}
        self._submission_mid: Dict[int, Optional[float]] = {}
        # Per position: the lots its open fills put in, less what its close fills took out
        self._position_lots: Dict[str, float] = {}

    def __call__(self, event: OrderEvent) -> None:
        """
        Write the line an order event becomes, if it becomes one.

        Args:
            event: The event the executor just recorded
        """
        if event.event_type is OrderEventType.SUBMITTED:
            self._phase_of[event.seq] = self._recorder.get_phase()
            self._submission_mid[event.seq] = event.submission_mid
            return
        line = FIELD_STUDY_LINE_BY_EVENT[event.event_type]
        if line is None:
            return
        # The phase that submitted THIS order: one the framework places later (a protective
        # order, a held-back close) keeps its own, and its stream row names the position
        submitted_in = (self._phase_of.get(event.submitted_seq)
                        if event.submitted_seq is not None else None)
        phase, phase_index = submitted_in or self._recorder.get_phase()
        if line == FILL_LINE:
            self._write_fill(event, phase, phase_index)
            return
        extra: Dict[str, Any] = self._shared_extra(event)
        if line == 'order_rejected':
            extra['reason'] = event.rejection_reason.value if event.rejection_reason else None
            extra['message'] = event.message
            extra['venue_reason'] = event.venue_reason
        self._recorder.write_projected(
            PLANE_BOT, line, phase, phase_index,
            order_id=event.order_id, side=_side_name(event.direction),
            lots=event.lots, status=event.event_type.value, detected_via='poll',
            extra=_without_none(extra))

    def write_broker_truth(self, record: BrokerTruthRecord) -> None:
        """
        Write a venue read as the study's snapshot or reconciliation line.

        At the session's start and end it is the `broker_snapshot` the certificate's flat gate and
        account delta read; a changed reconciliation is a `reconcile_alert`, divergent or clean.

        Args:
            record: What the venue answered
        """
        row = broker_truth_row(record, self._unit_name).model_dump(mode='json')
        orders = record.snapshot.venue_orders
        order_count = len(orders) if orders is not None else None
        extra = _without_none({
            'order_count': order_count,
            'unread_parts': row['unread_parts'] or None,
            'stream_seq': record.seq,
        })
        if record.read_reason is BrokerTruthReadReason.RECONCILE:
            phase, phase_index = self._recorder.get_phase()
            state = record.reconcile_state.value if record.reconcile_state is not None else None
            self._recorder.write_projected(
                PLANE_BROKER_TRUTH, 'reconcile_alert', phase, phase_index,
                status=state, reconcile={'state': state, 'divergence': row['divergence']},
                extra=extra)
            return
        phase = (PREFLIGHT_PHASE if record.read_reason is BrokerTruthReadReason.SESSION_START
                 else SESSION_END_PHASE)
        # The balances keep their key even when unread: None says the sheet could not be read,
        # and an empty sheet would say the account holds nothing
        extra['balances'] = record.snapshot.venue_balances
        if order_count is None:
            status = 'unread'
        else:
            status = 'flat' if order_count == 0 else 'not_flat'
        self._recorder.write_projected(
            PLANE_BROKER_TRUTH, 'broker_snapshot', phase, -1, status=status, extra=extra)

    def _write_fill(self, event: OrderEvent, phase: str, phase_index: int) -> None:
        """
        Write an execution as an open, a partial close or a full close.

        Args:
            event: A `filled` or `partially_filled` event
            phase: The phase that submitted the order
            phase_index: Its index
        """
        executed = event.lots or 0.0
        extra = self._shared_extra(event)
        extra['fee'] = event.fee
        extra['fee_currency'] = event.fee_currency
        line = 'order_filled'
        if event.action is OrderAction.CLOSE and event.position_id is not None:
            held = self._position_lots.get(event.position_id)
            if held is not None:
                remaining = max(held - executed, 0.0)
                self._position_lots[event.position_id] = remaining
                if remaining > _LOTS_EPSILON:
                    line = 'partial_close'
                    extra['remaining_lots'] = remaining
        elif event.position_id is not None:
            self._position_lots[event.position_id] = (
                self._position_lots.get(event.position_id, 0.0) + executed)
        self._recorder.write_projected(
            PLANE_BOT, line, phase, phase_index,
            order_id=event.order_id, side=_side_name(event.direction),
            lots=event.lots, price=event.fill_price, status=event.event_type.value,
            detected_via='poll', slippage=self._slippage_source(event),
            extra=_without_none(extra))

    def _slippage_source(self, event: OrderEvent) -> Dict[str, Any]:
        """
        What a fill is measured against — the values, not the measurement.

        The certificate computes the slippage; the line carries its sources. A market order
        against the mid when it was submitted, a limit against its own limit, a stop against its
        trigger: a stop fills at the market once triggered, and the distance is what it cost.

        Args:
            event: The fill

        Returns:
            Order type, trading side, what it is measured against and that price — the keys
            whose value is unknown are left out
        """
        order_type = event.order_type
        if order_type is OrderType.LIMIT:
            measured_against, price = 'limit_price', event.limit_price
        elif order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            measured_against, price = 'trigger_price', event.trigger_price
        else:
            measured_against = 'submission_mid'
            price = self._submission_mid.get(event.submitted_seq)
        side = _trading_side(event.direction, event.action)
        return _without_none({
            'order_type': order_type.value if order_type is not None else None,
            'side': side.value if side is not None else None,
            'measured_against': measured_against,
            'reference_price': price,
        })

    @staticmethod
    def _shared_extra(event: OrderEvent) -> Dict[str, Any]:
        """
        The fields every order line carries — what joins it to the order-event stream.

        Args:
            event: The event the line copies

        Returns:
            Its action, order type, submission and stream position, and on an ending who ended
            the order and why; a field the event does not state is left out
        """
        return {
            'action': event.action.value if event.action is not None else None,
            'order_type': event.order_type.value if event.order_type is not None else None,
            'submitted_seq': event.submitted_seq,
            'stream_seq': event.seq,
            'initiator': event.initiator.value if event.initiator is not None else None,
            'end_reason': event.end_reason.value if event.end_reason is not None else None,
        }


def _side_name(direction: Optional[OrderDirection]) -> Optional[str]:
    """
    The position's direction as the study's lines have always named it.

    Args:
        direction: The event's position direction

    Returns:
        'LONG' / 'SHORT', or None where the event names none
    """
    return direction.name if direction is not None else None


def _trading_side(
    direction: Optional[OrderDirection],
    action: Optional[OrderAction],
) -> Optional[OrderSide]:
    """
    Which side of the book an order traded, where the event states both halves of the answer.

    Args:
        direction: The POSITION's direction
        action: Open or close

    Returns:
        BUY or SELL from `direction_to_side`, or None where either is unknown
    """
    if direction is None or action is None:
        return None
    return direction_to_side(direction, action)


def _without_none(values: Dict[str, Any]) -> Dict[str, Any]:
    """
    A sub-block without its unset keys — the writer drops None at the top level only.

    Args:
        values: The block

    Returns:
        The block without None values
    """
    return {key: value for key, value in values.items() if value is not None}
