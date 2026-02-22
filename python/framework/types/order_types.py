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

from python.framework.utils.process_serialization_utils import serialize_value


# ============================================
# Order Type Enums
# ============================================

class OrderType(Enum):
    """
    Order type classification — used in OpenOrderRequest.order_type.

    Common (Tier 1 — all brokers):
        MARKET: Execute immediately at current market price
        LIMIT: Execute at specified price or better

    Extended (Tier 2 — broker-specific):
        STOP: Wait for trigger price, then execute as MARKET
        STOP_LIMIT: Wait for trigger price, then place LIMIT order
        TRAILING_STOP: Dynamic stop that follows price movement
        ICEBERG: Large order split into smaller visible chunks
    """
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"
    ICEBERG = "iceberg"


class OrderDirection(Enum):
    """Order direction (Buy or Sell)"""
    LONG = "long"
    SHORT = "short"

    def __str__(self) -> str:
        """String representation returns the enum value"""
        return self.value


class OrderStatus(Enum):
    """Order execution status"""
    PENDING = "pending"          # Order created, not yet sent
    SUBMITTED = "submitted"      # Sent to broker
    EXECUTED = "executed"        # Fully filled
    PARTIALLY_FILLED = "partial"  # Partially filled (large orders)
    REJECTED = "rejected"        # Broker rejected
    CANCELLED = "cancelled"      # User cancelled
    EXPIRED = "expired"          # Time-based expiration


class FillType(Enum):
    """
    How an order was filled — stored in OrderResult.metadata['fill_type'].

    Determines fill semantics:
        MARKET: Standard market fill at current tick price (taker fee)
        LIMIT: Limit order filled when price reached trigger level (maker fee)
        LIMIT_IMMEDIATE: Limit filled immediately after latency — price already past limit (maker fee)
        STOP: Stop trigger reached → filled at current market price (taker fee)
        STOP_LIMIT: Stop trigger reached → filled at limit price (maker fee)

    Note: No STOP_IMMEDIATE — if stop price is already exceeded after latency,
    the order fills immediately at current market price (same as STOP).
    """
    MARKET = "market"
    LIMIT = "limit"
    LIMIT_IMMEDIATE = "limit_immediate"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


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

    executed_price: Optional[float] = None
    executed_lots: Optional[float] = None
    execution_time: Optional[datetime] = None

    commission: float = 0.0
    swap: float = 0.0
    slippage_points: float = 0.0

    rejection_reason: Optional[RejectionReason] = None
    rejection_message: str = ""

    broker_order_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status in [OrderStatus.EXECUTED, OrderStatus.PARTIALLY_FILLED]

    @property
    def is_rejected(self) -> bool:
        return self.status == OrderStatus.REJECTED

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        return {
            'order_id': self.order_id,
            'status': self.status.value,

            'executed_price': self.executed_price,
            'executed_lots': self.executed_lots,
            'execution_time': self.execution_time.isoformat() if self.execution_time else None,

            'commission': self.commission,
            'swap': self.swap,
            'slippage_points': self.slippage_points,

            'rejection_reason': self.rejection_reason.value if self.rejection_reason else None,
            'rejection_message': self.rejection_message,

            'broker_order_id': self.broker_order_id,
            'metadata': serialize_value(self.metadata),
        }


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


# ============================================
# Open Order Request (internal pipeline object)
# ============================================

@dataclass
class OpenOrderRequest:
    """
    Bundled order parameters passed through the execution pipeline.

    Built by DecisionTradingAPI.send_order(), consumed by TradeSimulator/LiveTradeExecutor.

    Args:
        symbol: Trading symbol
        order_type: MARKET, LIMIT, STOP, or STOP_LIMIT
        direction: LONG or SHORT
        lots: Position size
        price: Limit price (required for LIMIT and STOP_LIMIT, None for MARKET/STOP)
        stop_price: Stop trigger price (required for STOP and STOP_LIMIT, None for MARKET/LIMIT)
        stop_loss: Optional stop loss price level on resulting position
        take_profit: Optional take profit price level on resulting position
        comment: Order comment
    """
    symbol: str
    order_type: OrderType
    direction: OrderDirection
    lots: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = ""


# ============================================
# Modification Result Types
# ============================================

class ModificationRejectionReason(Enum):
    """
    Reason why a position, limit order, or stop order modification was rejected.

    Used by modify_position(), modify_limit_order(), and modify_stop_order()
    to provide structured rejection feedback.
    """
    POSITION_NOT_FOUND = "position_not_found"
    LIMIT_ORDER_NOT_FOUND = "limit_order_not_found"
    STOP_ORDER_NOT_FOUND = "stop_order_not_found"
    INVALID_SL_LEVEL = "invalid_sl_level"
    INVALID_TP_LEVEL = "invalid_tp_level"
    SL_TP_CROSS = "sl_tp_cross"
    INVALID_PRICE = "invalid_price"
    NO_CURRENT_PRICE = "no_current_price"


@dataclass
class ModificationResult:
    """
    Result of a position or limit order modification attempt.

    Args:
        success: True if modification was applied
        rejection_reason: Reason for rejection (None if successful)
    """
    success: bool
    rejection_reason: Optional[ModificationRejectionReason] = None
