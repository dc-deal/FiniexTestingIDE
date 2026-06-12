"""
FiniexTestingIDE - Order Type System
Defines order types, capabilities, and execution results

Two-Tier Order System:
- Tier 1: Common Orders (all brokers MUST support)
- Tier 2: Extended Orders (broker-specific, opt-in)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, Dict, Optional

from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata
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


class OrderDirection(StrEnum):
    """
    Resulting position direction (internal to the executor and portfolio).

    LONG/SHORT describe what the position looks like after an order fills —
    the mechanic, not the intent. Decision logics should use OrderSide (the
    algo-facing enum); the executor resolves side → direction based on the
    broker's trading model.
    """
    LONG = "long"
    SHORT = "short"


class OrderSide(StrEnum):
    """
    Algo-facing order intent — what the strategy wants to do.

    BUY/SELL describe the intent, decoupled from the resulting position.
    The executor resolves OrderSide → OrderDirection based on trading model:
        Margin: BUY → LONG, SELL → SHORT (opens matching position)
        Spot:   BUY → LONG, SELL → SHORT (internal marker; spot branch
                handles base/quote balance movement)
    """
    BUY = "buy"
    SELL = "sell"


class OrderAction(Enum):
    """
    What the order is doing in the position lifecycle.

    OPEN: order creates or extends a position
    CLOSE: order reduces or closes a position (full or partial)

    First-class field on OrderResult (#330). Distinguishes open- and close-side
    OrderResults that share the same order_id (= position_id) — the
    EventStreamWriter needs this to emit distinct ORDER_SUBMIT / CLOSE_SUBMIT
    events for opens vs closes on the same position.
    """
    OPEN = "open"
    CLOSE = "close"


class CloseType(Enum):
    """
    Type of position close — full or partial.

    First-class field on close-side OrderResults (#343, set once filled) and
    on TradeRecord. Lives here (not in the trade-record types) because the
    trade-record module imports from this one — order_types is the base
    order-domain type module.
    """
    FULL = "full"
    PARTIAL = "partial"


def direction_to_side(direction: 'OrderDirection', action: OrderAction) -> 'OrderSide':
    """
    Map (position direction, lifecycle action) → execution side.

    Single source of truth for the BUY/SELL ↔ LONG/SHORT mapping. Used by
    every BrokerTrade construction site and by every TradeRecord builder that
    needs entry_side / exit_side derived from the position direction.

    Args:
        direction: Position direction (LONG/SHORT)
        action: Lifecycle action (OPEN/CLOSE)

    Returns:
        OrderSide.BUY  — open LONG, or close SHORT (buying back)
        OrderSide.SELL — close LONG, or open SHORT (short-sell)
    """
    if direction == OrderDirection.LONG:
        return OrderSide.BUY if action == OrderAction.OPEN else OrderSide.SELL
    return OrderSide.SELL if action == OrderAction.OPEN else OrderSide.BUY


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
    INSUFFICIENT_FUNDS = "insufficient_funds"
    INVALID_LOT_SIZE = "invalid_lot_size"
    SYMBOL_NOT_TRADEABLE = "symbol_not_tradeable"
    MARKET_CLOSED = "market_closed"
    INVALID_PRICE = "invalid_price"
    ORDER_TYPE_NOT_SUPPORTED = "order_type_not_supported"
    BROKER_ERROR = "broker_error"
    REJECTION_COOLDOWN = "rejection_cooldown"


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

    # Position-level features (#318)
    # native_position_sl_tp: broker supports server-side attached SL/TP on
    # open positions (MT5: True, Kraken Spot: False). When True, the executor
    # routes modify_position through the async pattern (processor.submit_
    # modify_position_async / sim _pending_position_modifications). When
    # False, modify_position falls back to instant local portfolio update.
    native_position_sl_tp: bool = False

    # Trade-record reporting (#326)
    # trade_level_reporting: broker exposes per-execution detail (Kraken
    # QueryTrades, MT5 HistoryDealsGet). When True, the executor queries
    # trade records on FILLED via the Tier-3 trades_query layer and
    # populates pending.fills.trades + cumulative_* aggregates. When False,
    # the executor synthesizes a single aggregate BrokerTrade from the
    # query response — the data model stays consistent.
    trade_level_reporting: bool = True

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

    position_id: Optional[str] = None

    # First-class action discriminator (#330). Distinguishes open and close
    # OrderResults that share the same order_id (= position_id). The
    # EventStreamWriter routes ORDER_SUBMIT vs CLOSE_SUBMIT based on this.
    # Defaults to None for legacy / EXPIRED / rejection paths where the
    # distinction does not apply.
    action: Optional[OrderAction] = None

    # Order dimensions promoted from the metadata bag (#343) — typed,
    # consistently present on PENDING/EXECUTED results. None on rejection
    # paths where the dimension does not apply.
    # direction: the position direction the order refers to (open: requested
    #   direction; close: direction of the position being closed).
    # requested_lots: lots the algo asked for (vs executed_lots = filled).
    # close_type: full/partial — close-side only, set once filled.
    symbol: Optional[str] = None
    direction: Optional[OrderDirection] = None
    requested_lots: Optional[float] = None
    close_type: Optional[CloseType] = None

    # Submission slippage audit (#340) — algo's trade-channel mid price at
    # the submission moment, propagated from PendingOrder. Surfaced in the
    # event-stream CSV (ORDER_SUBMIT / CLOSE_SUBMIT rows) so downstream
    # analysis can compute the per-fill slippage delta without rejoining
    # against the live audit pipeline. Empty for pre-tick rejections.
    submission: SubmissionMetadata = field(default_factory=SubmissionMetadata)

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

            'position_id': self.position_id,

            'action': self.action.value if self.action else None,
            'symbol': self.symbol,
            'direction': self.direction.value if self.direction else None,
            'requested_lots': self.requested_lots,
            'close_type': self.close_type.value if self.close_type else None,

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

    Built by DecisionTradingApi.send_order(), consumed by TradeSimulator/LiveTradeExecutor.

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
    # #318 — Async modify/cancel reject cases
    ORDER_NOT_CONFIRMED = "order_not_confirmed"   # broker_ref still None (Option A)
    OPERATION_BUSY = "operation_busy"             # another in_flight_operation already
    ORDER_TYPE_NOT_SUPPORTED = "order_type_not_supported"   # capability gate (e.g. stop_orders=False)


class ModificationStatus(Enum):
    """
    Lifecycle status of a modification request.

    PENDING: Accepted into the async pipeline, awaiting resolve (next-tick sim,
             drain_inbox live). Used as the return value of modify/cancel calls
             post-#318 — algos check has_in_flight_operation() to know when
             the operation has resolved.
    SUCCESS: Modification applied (synchronous fallback path, or resolved async
             outcome surfaced via outcome listener).
    REJECTED: Modification rejected (validation, broker, or async outcome).
    """
    PENDING = "pending"
    SUCCESS = "success"
    REJECTED = "rejected"


@dataclass
class ModificationResult:
    """
    Result of a position or limit order modification attempt.

    Args:
        success: True if modification was accepted (PENDING or SUCCESS).
                 Backward-compatible — algos checking `if result.success`
                 keep working under the post-#318 async path where the
                 modification is queued and resolves on the next tick / drain.
        status: Lifecycle status (PENDING for async-queued, SUCCESS for
                synchronous fallback or post-resolve, REJECTED on reject).
                Defaults to SUCCESS so legacy callers that don't set status
                explicitly behave unchanged.
        rejection_reason: Reason for rejection (None if successful or pending)
        order_id: Order/position id this modification targets — populated for
                  PENDING returns so the caller can use has_in_flight_operation()
                  to wait for resolve.
    """
    success: bool
    rejection_reason: Optional[ModificationRejectionReason] = None
    status: ModificationStatus = ModificationStatus.SUCCESS
    order_id: Optional[str] = None
