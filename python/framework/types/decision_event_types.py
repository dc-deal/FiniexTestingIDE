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
    ORDER_FILLED    → OrderFilledEvent    → on_order_filled
    ORDER_REJECTED  → OrderRejectedEvent  → on_order_rejected
    ORDER_CANCELLED → OrderCancelledEvent → on_order_cancelled
    PARTIAL_CLOSE   → PartialCloseEvent   → on_partial_close
    SESSION_END     → SessionEndEvent     → on_session_end
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional, Union

from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderResult,
    RejectionReason,
)


class DecisionEventType(StrEnum):
    """The set of events a decision logic can subscribe to via get_subscribed_events()."""
    ORDER_FILLED = 'order_filled'
    ORDER_REJECTED = 'order_rejected'
    ORDER_CANCELLED = 'order_cancelled'
    PARTIAL_CLOSE = 'partial_close'
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
    An order was rejected (at submission or at fill time). Delivered to on_order_rejected().

    Args:
        order_id: Internal order id
        direction: Position direction (LONG/SHORT)
        reason: Machine-readable rejection reason
        message: Human-readable rejection message
        result: Full OrderResult for detailed access
        tick_time: Tick timestamp at delivery (sim time / wall-clock)
    """
    order_id: str
    direction: OrderDirection
    reason: Optional[RejectionReason]
    message: str
    result: OrderResult
    tick_time: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class OrderCancelledEvent:
    """
    An active order was cancelled before fill. Delivered to on_order_cancelled().

    Args:
        order_id: Internal order id of the cancelled order
        direction: Position direction (LONG/SHORT), None if unknown
        tick_time: Tick timestamp at delivery (sim time / wall-clock)
    """
    order_id: str
    direction: Optional[OrderDirection]
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
    OrderCancelledEvent,
    PartialCloseEvent,
    SessionEndEvent,
]
