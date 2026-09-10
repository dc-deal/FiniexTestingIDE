# ============================================
# python/framework/types/live_execution_types.py
# ============================================
"""
FiniexTestingIDE - Live Execution Type Definitions
Data structures for live broker communication and order tracking.

Used by LiveTradeExecutor, LiveRequestProcessor, and broker adapters
for live order execution.

Architecture:
    BrokerResponse: Standardized broker reply (fill, rejection, status)
    BrokerOrderStatus: Broker-side order lifecycle states
    TimeoutConfig: Configurable timeout thresholds for order monitoring
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class BrokerOrderStatus(Enum):
    """
    Broker-side order status.

    Represents the lifecycle of an order at the broker level.
    Mapped from broker-specific status codes to this unified enum.

    UNRESOLVED is the one value here that is OURS rather than the venue's (#473): the
    request did not complete, so the venue may or may not hold this order. It exists
    because "the venue refused this order" and "we could not reach the venue" are
    different facts, and reporting the second as the first is how an orphan is born —
    we forget an order that is resting at the broker.

    UNKNOWN is the third fact in that family and the one that was missing: the venue
    ANSWERED, and its answer says nothing about this order. Measured 2026-09-08 against
    Kraken — a QueryOrders for a txid it never minted returns an empty result, and reading
    a status off the absent entry produced PENDING, i.e. "it is still working". "The venue
    has never heard of this order" and "the order is resting" are opposite facts, and only
    one of them is safe to act on. Like UNRESOLVED it is NOT terminal, but for the opposite
    reason: asking again the same way will give the same non-answer, so what it calls for is
    a WIDER read (a time-ranged history rather than a reference lookup), never a booking.
    """
    PENDING = 'pending'
    FILLED = 'filled'
    PARTIALLY_FILLED = 'partially_filled'
    REJECTED = 'rejected'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'
    UNRESOLVED = 'unresolved'
    UNKNOWN = 'unknown'


@dataclass
class BrokerResponse:
    """
    Standardized response from broker API.

    Wraps broker-specific response formats into a unified structure.
    Used by AbstractAdapter Tier-3 layers (_parse_*_response) and the
    LiveRequestProcessor sync/async orchestrators.

    Args:
        broker_ref: Broker's order reference ID (e.g., Kraken txid)
        status: Current order status at broker
        fill_price: Execution price (set when status=FILLED)
        filled_lots: Actual filled volume (set when status=FILLED)
        rejection_reason: Broker's rejection message (set when status=REJECTED)
        undecided_reason: Why this poll produced no decision — set only by the DRY-RUN
            simulator, which plays the venue and can run out of the facts it needs (#505).
            It is never a venue answer: a real broker either knows the order's state or
            cannot be reached, and the second case is UNRESOLVED. A response carrying this
            stays PENDING, and the executor is what makes it visible (§35), because the
            adapter has no logger by design
        timestamp: Broker response timestamp (UTC)
        raw_response: Preserved broker-specific response for debugging
    """
    broker_ref: str
    status: BrokerOrderStatus
    fill_price: Optional[float] = None
    filled_lots: Optional[float] = None
    rejection_reason: Optional[str] = None
    undecided_reason: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_response: Optional[Dict[str, Any]] = None

    @property
    def is_filled(self) -> bool:
        """Order was fully executed."""
        return self.status == BrokerOrderStatus.FILLED

    @property
    def is_rejected(self) -> bool:
        """Order was rejected by broker."""
        return self.status == BrokerOrderStatus.REJECTED

    @property
    def is_unresolved(self) -> bool:
        """We could not reach the venue — its answer, if any, was lost (#473)."""
        return self.status == BrokerOrderStatus.UNRESOLVED

    @property
    def is_unknown(self) -> bool:
        """The venue answered and named no such order — not a state, an absence."""
        return self.status == BrokerOrderStatus.UNKNOWN

    @property
    def is_terminal(self) -> bool:
        """
        Order reached a final state (no further updates expected).

        UNRESOLVED is deliberately NOT terminal: it is the absence of an answer, so the
        one thing that must still happen is asking again. UNKNOWN is not terminal either,
        and treating it as one would be worse than the PENDING it replaces: booking a
        cancel or an expiry off an empty answer invents a fact about an order the venue
        did not describe.
        """
        return self.status in (
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.REJECTED,
            BrokerOrderStatus.CANCELLED,
            BrokerOrderStatus.EXPIRED,
        )


@dataclass
class TimeoutConfig:
    """
    Timeout configuration for live order monitoring.

    Args:
        order_timeout_seconds: Max wait time for broker fill/rejection
    """
    order_timeout_seconds: float = 30.0
