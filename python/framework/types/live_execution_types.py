# ============================================
# python/framework/types/live_execution_types.py
# ============================================
"""
FiniexTestingIDE - Live Execution Type Definitions
Data structures for live broker communication and order tracking.

Used by LiveTradeExecutor, LiveOrderTracker, and broker adapters
for live order execution (Horizon 2).

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
    """
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class BrokerResponse:
    """
    Standardized response from broker API.

    Wraps broker-specific response formats into a unified structure.
    Used by AbstractAdapter.execute_order(), check_order_status(), cancel_order().

    Args:
        broker_ref: Broker's order reference ID (e.g., Kraken txid)
        status: Current order status at broker
        fill_price: Execution price (set when status=FILLED)
        filled_lots: Actual filled volume (set when status=FILLED)
        rejection_reason: Broker's rejection message (set when status=REJECTED)
        timestamp: Broker response timestamp (UTC)
        raw_response: Preserved broker-specific response for debugging
    """
    broker_ref: str
    status: BrokerOrderStatus
    fill_price: Optional[float] = None
    filled_lots: Optional[float] = None
    rejection_reason: Optional[str] = None
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
    def is_terminal(self) -> bool:
        """Order reached a final state (no further updates expected)."""
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

    Controls how long LiveTradeExecutor waits for broker responses
    and how frequently it polls for updates.

    Args:
        order_timeout_seconds: Max wait time for broker fill/rejection
        poll_interval_seconds: Interval between broker status checks
    """
    order_timeout_seconds: float = 30.0
    poll_interval_seconds: float = 1.0
