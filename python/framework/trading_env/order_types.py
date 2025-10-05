"""
FiniexTestingIDE - Order Type System
Defines order types, capabilities, and execution results

Two-Tier Order System:
- Tier 1: Common Orders (all brokers MUST support)
- Tier 2: Extended Orders (broker-specific, opt-in)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


# ============================================
# Order Type Enums
# ============================================

class OrderType(Enum):
    """
    Order type classification.

    Common (Tier 1): MARKET, LIMIT
    Extended (Tier 2): STOP, STOP_LIMIT, TRAILING_STOP, ICEBERG
    """
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"
    ICEBERG = "iceberg"


class OrderDirection(Enum):
    """Order direction (Buy or Sell)"""
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    """Order execution status"""
    PENDING = "pending"          # Order created, not yet sent
    SUBMITTED = "submitted"      # Sent to broker
    EXECUTED = "executed"        # Fully filled
    PARTIALLY_FILLED = "partial"  # Partially filled (large orders)
    REJECTED = "rejected"        # Broker rejected
    CANCELLED = "cancelled"      # User cancelled
    EXPIRED = "expired"          # Time-based expiration


class RejectionReason(Enum):
    """Reasons why orders get rejected"""
    INSUFFICIENT_MARGIN = "insufficient_margin"
    INVALID_LOT_SIZE = "invalid_lot_size"
    SYMBOL_NOT_TRADEABLE = "symbol_not_tradeable"
    MARKET_CLOSED = "market_closed"
    INVALID_PRICE = "invalid_price"
    ORDER_TYPE_NOT_SUPPORTED = "order_type_not_supported"
    BROKER_ERROR = "broker_error"


# ============================================
# Order Capability System
# ============================================

@dataclass
class OrderCapabilities:
    """
    Runtime capability checks for broker-specific order types.

    Common capabilities (all brokers):
    - market_orders, limit_orders

    Extended capabilities (broker-specific):
    - stop_orders, stop_limit_orders, trailing_stop, iceberg_orders
    """
    # Tier 1: Common Orders
    market_orders: bool = True
    limit_orders: bool = True

    # Tier 2: Extended Orders
    stop_orders: bool = False
    stop_limit_orders: bool = False
    trailing_stop: bool = False
    iceberg_orders: bool = False

    # Additional broker features
    hedging_allowed: bool = False
    partial_fills_supported: bool = False

    def supports_order_type(self, order_type: OrderType) -> bool:
        """Check if broker supports specific order type"""
        mapping = {
            OrderType.MARKET: self.market_orders,
            OrderType.LIMIT: self.limit_orders,
            OrderType.STOP: self.stop_orders,
            OrderType.STOP_LIMIT: self.stop_limit_orders,
            OrderType.TRAILING_STOP: self.trailing_stop,
            OrderType.ICEBERG: self.iceberg_orders,
        }
        return mapping.get(order_type, False)


# ============================================
# Order Definitions
# ============================================

@dataclass
class BaseOrder:
    """Base order structure (common fields)"""
    symbol: str
    direction: OrderDirection
    lots: float
    order_type: OrderType

    # Optional fields
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = ""
    magic_number: int = 0

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)

    def validate(self, min_lot: float, max_lot: float, lot_step: float) -> bool:
        """Validate lot size against broker limits"""
        if self.lots < min_lot or self.lots > max_lot:
            return False

        # Check lot step (e.g., 0.01 for Forex)
        if lot_step > 0:
            remainder = (self.lots - min_lot) % lot_step
            if abs(remainder) > 1e-8:  # Floating point tolerance
                return False

        return True


@dataclass
class MarketOrder(BaseOrder):
    """
    Market Order - Execute immediately at current market price

    Common to ALL brokers (Tier 1)
    """
    order_type: OrderType = field(default=OrderType.MARKET, init=False)

    # Slippage tolerance (points)
    max_slippage: Optional[int] = None


@dataclass
class LimitOrder(BaseOrder):
    """
    Limit Order - Execute at specified price or better

    Common to ALL brokers (Tier 1)
    """
    order_type: OrderType = field(default=OrderType.LIMIT, init=False)

    # Required: Entry price
    price: float = 0.0

    # Optional: Expiration
    expiration: Optional[datetime] = None


@dataclass
class StopOrder(BaseOrder):
    """
    Stop Order - Becomes market order when price reaches stop level

    Extended feature (Tier 2) - MT5: yes, Kraken: no
    """
    order_type: OrderType = field(default=OrderType.STOP, init=False)

    # Required: Stop trigger price
    stop_price: float = 0.0


@dataclass
class StopLimitOrder(BaseOrder):
    """
    Stop-Limit Order - Becomes limit order when stop price reached

    Extended feature (Tier 2) - MT5: yes, Kraken: yes
    """
    order_type: OrderType = field(default=OrderType.STOP_LIMIT, init=False)

    # Required: Stop and limit prices
    stop_price: float = 0.0
    limit_price: float = 0.0


@dataclass
class IcebergOrder(BaseOrder):
    """
    Iceberg Order - Large order split into smaller visible chunks

    Extended feature (Tier 2) - MT5: no, Kraken: yes
    """
    order_type: OrderType = field(default=OrderType.ICEBERG, init=False)

    # Required: Visible portion size
    visible_lots: float = 0.0

    # Limit price
    price: float = 0.0


# ============================================
# Order Execution Results
# ============================================

@dataclass
class OrderResult:
    """
    Result of order execution attempt.

    Contains execution details, status, and broker feedback.
    """
    order_id: str
    status: OrderStatus

    # Execution details (if executed)
    executed_price: Optional[float] = None
    executed_lots: Optional[float] = None
    execution_time: Optional[datetime] = None

    # Commission and fees
    commission: float = 0.0
    swap: float = 0.0

    # Slippage (for market orders)
    slippage_points: float = 0.0

    # Rejection details (if rejected)
    rejection_reason: Optional[RejectionReason] = None
    rejection_message: str = ""

    # Broker-specific metadata
    broker_order_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Check if order was successfully executed"""
        return self.status in [OrderStatus.EXECUTED, OrderStatus.PARTIALLY_FILLED]

    @property
    def is_rejected(self) -> bool:
        """Check if order was rejected"""
        return self.status == OrderStatus.REJECTED


# ============================================
# Helper Functions
# ============================================

def create_rejection_result(
    order_id: str,
    reason: RejectionReason,
    message: str = ""
) -> OrderResult:
    """Create standardized rejection result"""
    return OrderResult(
        order_id=order_id,
        status=OrderStatus.REJECTED,
        rejection_reason=reason,
        rejection_message=message
    )
