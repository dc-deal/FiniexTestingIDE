"""
FiniexTestingIDE - Order Event Types (#362)

One record per order TRANSITION — the order-event stream. The order history keeps one row per
ending; this keeps everything in between as well: the submission, the venue taking the order, a
stop triggering, every cancel and amend asked for and how it was answered, an answer that was
lost and the asking that settled it, and the adoption of an order a previous session sent.

The vocabulary follows FIX's execution reports and nautilus_trader's order events: what happened
is the event type, never inferred from where the order stands.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, FrozenSet, Optional

from python.framework.types.trading_env_types.executor_mode_types import ExecutorMode
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderStatus,
    OrderType,
    RejectionReason,
)


class OrderEventType(Enum):
    """What happened to an order — one member per transition."""
    SUBMITTED = 'submitted'                # Handed to the venue — the simulated one included
    ACCEPTED = 'accepted'                  # The venue took it: answered with a reference, started resting, or filled in its answer
    REJECTED = 'rejected'                  # The venue refused it — the simulated one included
    DENIED = 'denied'                      # Refused here before anything was sent
    UNRESOLVED = 'unresolved'              # A write's answer was lost — `operation` says which
    RESOLVED = 'resolved'                  # Asking the venue settled a lost answer: it names the order
    TRIGGERED = 'triggered'                # A stop reached its price; a stop-limit becomes a limit
    MODIFY_REQUESTED = 'modify_requested'  # An amend was sent
    MODIFIED = 'modified'                  # The amend took effect
    MODIFY_REJECTED = 'modify_rejected'    # The venue refused the amend, or a fill overtook it
    CANCEL_REQUESTED = 'cancel_requested'  # A cancel was sent
    CANCEL_DEFERRED = 'cancel_deferred'    # A cancel was parked: the order had no venue reference yet
    CANCELLED = 'cancelled'                # The order ended by a cancel
    CANCEL_REJECTED = 'cancel_rejected'    # The venue refused the cancel, or a fill overtook it
    PARTIALLY_FILLED = 'partially_filled'  # Part of the order executed; the rest is still working or has ended
    FILLED = 'filled'                      # The order executed
    EXPIRED = 'expired'                    # The order ran out of time — the data's end, or the venue's
    UNDELIVERED = 'undelivered'            # The venue confirms it never received the order
    UNACCOUNTED = 'unaccounted'            # We stopped asking; the venue may hold the order
    ADOPTED = 'adopted'                    # An order a previous session sent, taken over at boot


class OrderEventPlane(Enum):
    """Whose account of an order a record is."""
    BOT = 'bot'                    # What this process did and was told
    # What the venue reports when a live session asks it — a BrokerTruthRecord, in this same
    # stream and under the same seq (broker_truth_types, #362)
    BROKER_TRUTH = 'broker_truth'


class OrderOperation(Enum):
    """Which request a lost answer — or the asking that settled it — is about."""
    SUBMIT = 'submit'
    CANCEL = 'cancel'
    MODIFY = 'modify'
    STATUS_READ = 'status_read'    # The read a fill timeout asks with


# The event an ENDING row is recorded as. Every ending goes through the executor's one booking
# point, so no row exists without its event. `pending` is a submission's row, not an ending:
# its event is written where the submission is counted.
ORDER_EVENT_BY_STATUS: Dict[OrderStatus, Optional[OrderEventType]] = {
    OrderStatus.PENDING: None,
    OrderStatus.EXECUTED: OrderEventType.FILLED,
    OrderStatus.DENIED: OrderEventType.DENIED,
    OrderStatus.REJECTED: OrderEventType.REJECTED,
    OrderStatus.CANCELLED: OrderEventType.CANCELLED,
    OrderStatus.EXPIRED: OrderEventType.EXPIRED,
    OrderStatus.UNDELIVERED: OrderEventType.UNDELIVERED,
    OrderStatus.UNACCOUNTED: OrderEventType.UNACCOUNTED,
}

# The events that end an order's life in a unit — what a late answer is told about. Derived
# from the map above, so a new ending status cannot be left out of it.
ENDING_EVENT_TYPES: FrozenSet[OrderEventType] = frozenset(
    event for event in ORDER_EVENT_BY_STATUS.values() if event is not None)


class InFlightEnding(Enum):
    """How an order's in-flight phase ended — the venue's first word on a submission."""
    ACCEPTED = 'accepted'                  # The venue took the order
    REJECTED = 'rejected'                  # The venue refused it
    NEVER_CONFIRMED = 'never_confirmed'    # The venue never confirmed it: undelivered, unaccounted
    EXPIRED = 'expired'                    # The data's end met it on its way


# Which event ends a submission's in-flight phase, and as what — the pending-order counters are
# a fold over the stream with this map, one count per category. Only a submission's FIRST such
# event counts: a resting order the venue took and that later expires was accepted, not expired.
# A member that ends no in-flight phase says why beside it.
IN_FLIGHT_ENDING_BY_EVENT: Dict[OrderEventType, Optional[InFlightEnding]] = {
    OrderEventType.SUBMITTED: None,           # opens the phase; counted as the submission itself
    OrderEventType.ACCEPTED: InFlightEnding.ACCEPTED,
    OrderEventType.REJECTED: InFlightEnding.REJECTED,
    OrderEventType.DENIED: None,              # refused before anything was sent
    OrderEventType.UNRESOLVED: None,          # a question; its answer is the ending that follows
    OrderEventType.RESOLVED: None,            # the acceptance or the ending after it says how
    OrderEventType.TRIGGERED: None,           # the venue took the order before it could trigger
    OrderEventType.MODIFY_REQUESTED: None,    # a working order is modified, never one on its way
    OrderEventType.MODIFIED: None,
    OrderEventType.MODIFY_REJECTED: None,
    OrderEventType.CANCEL_REQUESTED: None,
    OrderEventType.CANCEL_DEFERRED: None,     # parked until the venue answers the submission
    OrderEventType.CANCELLED: None,           # a cancel reaches an order the venue took
    OrderEventType.CANCEL_REJECTED: None,
    OrderEventType.PARTIALLY_FILLED: None,    # every execution follows the acceptance
    OrderEventType.FILLED: None,
    OrderEventType.EXPIRED: InFlightEnding.EXPIRED,
    OrderEventType.UNDELIVERED: InFlightEnding.NEVER_CONFIRMED,
    OrderEventType.UNACCOUNTED: InFlightEnding.NEVER_CONFIRMED,
    OrderEventType.ADOPTED: None,             # an earlier session's order, accepted there
}

_BOTH = frozenset({ExecutorMode.SIMULATION, ExecutorMode.LIVE})
_SIMULATION = frozenset({ExecutorMode.SIMULATION})
_LIVE = frozenset({ExecutorMode.LIVE})

# Which pipeline emits each member. A member one pipeline cannot produce says why beside it —
# without this a completeness check is satisfied by a member nothing ever writes.
ORDER_EVENT_PIPELINES: Dict[OrderEventType, FrozenSet[ExecutorMode]] = {
    OrderEventType.SUBMITTED: _BOTH,
    OrderEventType.ACCEPTED: _BOTH,
    OrderEventType.REJECTED: _BOTH,
    OrderEventType.DENIED: _BOTH,
    OrderEventType.UNRESOLVED: _LIVE,         # the simulation has no transport to lose an answer on
    OrderEventType.RESOLVED: _LIVE,
    OrderEventType.TRIGGERED: _SIMULATION,    # no venue traded today reports a trigger; live sees the fill
    OrderEventType.MODIFY_REQUESTED: _BOTH,
    OrderEventType.MODIFIED: _BOTH,
    OrderEventType.MODIFY_REJECTED: _BOTH,
    OrderEventType.CANCEL_REQUESTED: _BOTH,
    OrderEventType.CANCEL_DEFERRED: _LIVE,    # the simulation refuses a cancel of an order on its way (#567)
    OrderEventType.CANCELLED: _BOTH,
    OrderEventType.CANCEL_REJECTED: _BOTH,
    OrderEventType.PARTIALLY_FILLED: _LIVE,   # the simulation never fills part of an order
    OrderEventType.FILLED: _BOTH,
    OrderEventType.EXPIRED: _BOTH,
    OrderEventType.UNDELIVERED: _LIVE,        # only a venue can say it never received an order
    OrderEventType.UNACCOUNTED: _LIVE,        # a simulated venue always answers
    OrderEventType.ADOPTED: _LIVE,            # a backtest starts with no orders to take over
}


@dataclass(slots=True)
class OrderEvent:
    """
    One transition in an order's life — one line of the order-event stream.

    Args:
        seq: Strictly increasing within the unit — THE order of the stream. Never a timestamp:
            several steps often carry the same instant
        event_type: What happened
        order_id: The order's own id
        submitted_seq: `seq` of the submission this event belongs to — of its adoption, for an
            order a previous session sent. The join key: one `order_id` repeats across the
            closes of a position. None only on `denied`, which was never submitted
        record_plane: Whose account this is — this process's, or the venue's when asked
        position_id: The position the order opens or closes
        action: Open or close
        order_type: The type the order was asked as; None where it is not known
        symbol: The instrument
        direction: The POSITION's direction — for a close that of the position it closes,
            which a protective order's own trading side is the opposite of
        client_order_id: Our wire key
        broker_ref: The venue's handle for the order
        previous_broker_ref: The handle before an amend replaced it
        trade_id: The execution, where the event is exactly one
        lots: This event's quantity — asked for, executed, or ended
        cum_lots: What the order has executed so far
        fill_price: The execution price
        limit_price: The order's limit
        trigger_price: The order's stop
        fee: The fee charged on this execution
        fee_currency: Its currency
        submission_mid: The market's mid when the order was submitted
        submission_time_msc: That market moment, epoch milliseconds
        in_flight_ms: On the acceptance or refusal that answers a submission: how long the
            answer took — the modelled delay in a backtest, a monotonic measurement live
        event_time: When it happened, on the run's canonical clock. None only before the clock
            has been set — an adoption at boot
        ts_init: When this process saw it, on the wall clock — live only; None in a backtest,
            which has to stay reproducible
        initiator: Who ended the order, on an ending nobody refused
        end_reason: Why it ended
        rejection_reason: Why it was refused
        venue_reason: The venue's own code, passed through and read by nothing above the adapter
        message: The human sentence beside it
        lost_request: On `unresolved` and `resolved`, which request's answer the question is
            about
    """
    seq: int
    event_type: OrderEventType
    order_id: str
    submitted_seq: Optional[int] = None
    record_plane: OrderEventPlane = OrderEventPlane.BOT
    position_id: Optional[str] = None
    action: Optional[OrderAction] = None
    order_type: Optional[OrderType] = None
    symbol: Optional[str] = None
    direction: Optional[OrderDirection] = None
    client_order_id: Optional[str] = None
    broker_ref: Optional[str] = None
    previous_broker_ref: Optional[str] = None
    trade_id: Optional[str] = None
    lots: Optional[float] = None
    cum_lots: Optional[float] = None
    fill_price: Optional[float] = None
    limit_price: Optional[float] = None
    trigger_price: Optional[float] = None
    fee: Optional[float] = None
    fee_currency: Optional[str] = None
    submission_mid: Optional[float] = None
    submission_time_msc: Optional[int] = None
    in_flight_ms: Optional[float] = None
    event_time: Optional[datetime] = None
    ts_init: Optional[datetime] = None
    initiator: Optional[OrderInitiator] = None
    end_reason: Optional[OrderEndReason] = None
    rejection_reason: Optional[RejectionReason] = None
    venue_reason: Optional[str] = None
    message: Optional[str] = None
    lost_request: Optional[OrderOperation] = None
