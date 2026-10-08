"""
FiniexTestingIDE - Decision Event Types

Typed event model for the Decision Event Channel (#348).

This is the single reference for every event a decision logic can subscribe to:
each DecisionEventType maps to one typed payload and one hook method on
AbstractDecisionLogic. The decision logic declares the events it wants via
get_subscribed_events(); the DecisionEventDispatcher delivers the matching
payloads to the on_* hooks at the tick-loop boundary.

The channel is source-agnostic: an event carries the same payload whether it
originated from the simulation latency path, live REST polling (#320), or a
future WebSocket push (#331). Each executor emits only the events it can produce
truthfully (see the emit matrix in the channel architecture doc).

Event → payload → hook map:
    ORDER_FILLED      → OrderFilledEvent      → on_order_filled
    ORDER_REJECTED    → OrderRejectedEvent    → on_order_rejected
    ORDER_UNACCOUNTED → OrderUnaccountedEvent → on_order_unaccounted
    ORDER_CANCELLED   → OrderCancelledEvent   → on_order_cancelled
    PARTIAL_CLOSE     → PartialCloseEvent     → on_partial_close
    POSITION_CLOSED   → PositionClosedEvent   → on_position_closed
    SESSION_END       → SessionEndEvent       → on_session_end
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional, Union

from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderEndReason,
    OrderResult,
    RejectionReason,
)


class DecisionEventType(StrEnum):
    """The set of events a decision logic can subscribe to via get_subscribed_events()."""
    ORDER_FILLED = 'order_filled'
    ORDER_REJECTED = 'order_rejected'
    ORDER_UNACCOUNTED = 'order_unaccounted'
    ORDER_CANCELLED = 'order_cancelled'
    PARTIAL_CLOSE = 'partial_close'
    POSITION_CLOSED = 'position_closed'
    SESSION_END = 'session_end'


class SessionEndSeverity(StrEnum):
    """
    Severity of a session-end request — controls cleanup behaviour.

    NORMAL: graceful — close remaining orders + final stats + clean exit.
    EMERGENCY: immediate exit, best-effort cleanup (second-Ctrl+C semantics).
    """
    NORMAL = 'normal'
    EMERGENCY = 'emergency'


@dataclass(frozen=True, slots=True)
class OrderFilledEvent:
    """
    An order reached a filled state. Delivered to on_order_filled().

    Args:
        order_id: Internal order id (equals position_id for opens)
        position_id: Resulting position id (None for close-side fills without a new position)
        direction: Position direction (LONG/SHORT)
        fill_price: Executed price
        lots: Executed lots
        result: Full OrderResult for detailed access
        tick_time: Tick timestamp at delivery (sim time / wall-clock)
    """
    order_id: str
    position_id: Optional[str]
    direction: OrderDirection
    fill_price: Optional[float]
    lots: Optional[float]
    result: OrderResult
    tick_time: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class OrderRejectedEvent:
    """
    An order did not reach the venue's book after send_order() returned it as pending. Delivered to on_order_rejected().

    Four sources: the venue refused it (live), the simulation's funds or margin check refused
    it at the fill, the stress test refused it in a backtest, or the venue confirmed it never
    received the order (live). `result.status` tells the last one apart: `undelivered`, with no
    reason, where the other three are `rejected`. A refusal send_order() returns directly —
    order guard, lot size, funds at submission — never arrives here: that return value is the
    answer, and its status is `denied`.

    An order the framework gave up waiting for is NOT here any more: whether the venue holds it
    is unknown, and that arrives as OrderUnaccountedEvent.

    Args:
        order_id: Internal order id
        direction: Position direction (LONG/SHORT) — for a close, the direction of the
            position it closes; None when that position is no longer held
        reason: Machine-readable rejection reason; None for an undelivered order
        message: Human-readable rejection message
        result: Full OrderResult for detailed access
        tick_time: Tick timestamp at delivery (sim time / wall-clock)
    """
    order_id: str
    direction: Optional[OrderDirection]
    reason: Optional[RejectionReason]
    message: str
    result: OrderResult
    tick_time: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class OrderUnaccountedEvent:
    """
    The framework stopped asking about an order the venue may still hold. Delivered to on_order_unaccounted().

    Live only. It fires when an order's answer never came and the venue could not be asked what
    became of it — at its fill timeout, or when the resolution ran out of attempts. It may have
    filled: no fill was booked, and what the account holds now is the reconciliation's to
    establish. A strategy that sizes from its own open positions should treat the order as
    possibly filled.

    Not at the end of the session: an order still unconfirmed then is booked `unaccounted` in
    the order history, but the session's last pass has run by that point, and no hook is called
    after it.

    Args:
        order_id: Internal order id
        direction: Position direction (LONG/SHORT) — for a close, the direction of the
            position it closes; None when that position is no longer held
        end_reason: Why the framework stopped asking — the timeout or the resolution ceiling
        result: Full OrderResult for detailed access
        tick_time: Tick timestamp at delivery (sim time / wall-clock)
    """
    order_id: str
    direction: Optional[OrderDirection]
    end_reason: Optional[OrderEndReason]
    result: OrderResult
    tick_time: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class OrderCancelledEvent:
    """
    An active order ended before it filled — cancelled, or expired at the venue. Delivered to on_order_cancelled().

    `result` is its booked row: `result.initiator` says whether the strategy, the framework or
    the venue ended it, `result.end_reason` why, and `result.status` is `expired` where the venue
    let the order run out rather than cancelling it.

    Args:
        order_id: Internal order id of the cancelled order
        direction: Position direction (LONG/SHORT), None if unknown
        result: The order's booked row
        tick_time: Tick timestamp at delivery (sim time / wall-clock)
    """
    order_id: str
    direction: Optional[OrderDirection]
    result: OrderResult
    tick_time: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class PartialCloseEvent:
    """
    A position was partially closed (lots remain open). Delivered to on_partial_close().

    Args:
        position_id: Position that was partially closed
        direction: Position direction (LONG/SHORT)
        closed_lots: Lots closed by this fill
        remaining_lots: Lots still open after this fill
        fill_price: Executed close price
        result: Full OrderResult for detailed access
        tick_time: Tick timestamp at delivery (sim time / wall-clock)
    """
    position_id: str
    direction: OrderDirection
    closed_lots: float
    remaining_lots: float
    fill_price: Optional[float]
    result: OrderResult
    tick_time: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class PositionClosedEvent:
    """
    A position closed completely. Delivered to on_position_closed().

    Fires in BOTH pipelines on EVERY full close, not only on a venue-initiated one. Two
    reasons it is not narrowed to #503's case: a live-only event is a parity break of the
    same family the external-data contract forbids, and a strategy reacting to "my
    position is gone" needs it whichever route closed it. Until now a full close emitted
    nothing at all — the algo learned of it by noticing the position missing from
    get_open_positions().

    Args:
        position_id: The position that closed
        direction: Position direction (LONG/SHORT)
        close_reason: Why it closed (SL_TRIGGERED, TP_TRIGGERED, MANUAL, ...)
        requested_locally: False when the VENUE initiated it — a protective order the
            venue held fired while nobody here asked for a close (#503)
        fill_price: Executed close price
        lots: Lots closed
        realized_pnl: Realised P&L of the close
        result: Full OrderResult for detailed access
        tick_time: Tick timestamp at delivery (sim time / wall-clock)
    """
    position_id: str
    direction: OrderDirection
    close_reason: CloseReason
    requested_locally: bool
    fill_price: Optional[float]
    lots: Optional[float]
    realized_pnl: float
    result: OrderResult
    tick_time: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class SessionEndEvent:
    """
    The trading session is ending. Delivered to on_session_end().

    Fired on bot request (request_session_end), tick-source exhaustion (sim),
    operator Ctrl+C, or a safety halt.

    Args:
        reason: Human-readable reason for the session end
        severity: NORMAL (graceful) or EMERGENCY (immediate)
        tick_time: Tick timestamp at delivery (sim time / wall-clock)
    """
    reason: str
    severity: SessionEndSeverity
    tick_time: Optional[datetime] = None


# Union of all event payloads — the buffer element type carried by the dispatcher.
DecisionEvent = Union[
    OrderFilledEvent,
    OrderRejectedEvent,
    OrderUnaccountedEvent,
    OrderCancelledEvent,
    PartialCloseEvent,
    PositionClosedEvent,
    SessionEndEvent,
]
