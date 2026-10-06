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

    Read-only:
        UNKNOWN: A resting order the venue reports under a type this project cannot name.
            It exists so a truth-pull never has to choose between dropping the row and
            mislabelling it — both are worse. Dropping it hides the order from the
            exclusive-account check, which asks whether a stranger is working our symbol;
            calling it a LIMIT puts a number the venue meant as an offset into a field the
            whole codebase reads as a limit price. An UNKNOWN row carries no prices, and
            nothing routable admits the type, so it can be reported and not acted upon.
            Never valid on an OpenOrderRequest — the request gates refuse it like any type
            the pipeline has not built.
    """
    MARKET = 'market'
    LIMIT = 'limit'
    STOP = 'stop'
    STOP_LIMIT = 'stop_limit'
    TRAILING_STOP = 'trailing_stop'
    ICEBERG = 'iceberg'
    UNKNOWN = 'unknown'


# Order types that actually REST at a venue — a placed order waiting for a price rather
# than one in flight. A MARKET order in a venue's open list is in transit, not resting, and
# adopting one would put it into a world where nothing triggers it. Declared here because
# both the boot adopter and the live drain need the same answer, and two copies of it drift.
RESTING_ORDER_TYPES = frozenset({
    OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT,
})


class OrderDirection(StrEnum):
    """
    Resulting position direction (internal to the executor and portfolio).

    LONG/SHORT describe what the position looks like after an order fills —
    the mechanic, not the intent. Decision logics should use OrderSide (the
    algo-facing enum); the executor resolves side → direction based on the
    broker's trading model.
    """
    LONG = 'long'
    SHORT = 'short'


class OrderSide(StrEnum):
    """
    Algo-facing order intent — what the strategy wants to do.

    BUY/SELL describe the intent, decoupled from the resulting position.
    The executor resolves OrderSide → OrderDirection based on trading model:
        Margin: BUY → LONG, SELL → SHORT (opens matching position)
        Spot:   BUY → LONG, SELL → SHORT (internal marker; spot branch
                handles base/quote balance movement)
    """
    BUY = 'buy'
    SELL = 'sell'


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
    OPEN = 'open'
    CLOSE = 'close'


class CloseType(Enum):
    """
    Type of position close — full or partial.

    First-class field on close-side OrderResults (#343, set once filled) and
    on TradeRecord. Lives here (not in the trade-record types) because the
    trade-record module imports from this one — order_types is the base
    order-domain type module.
    """
    FULL = 'full'
    PARTIAL = 'partial'


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
    """
    Where an order stands: PENDING while it has not ended, and one status per way it can end.

    Each ending has its own word because each is acted on differently. A DENIED order never
    left this process; a REJECTED one was refused by the venue; an UNACCOUNTED one may still
    be working there. They used to share `rejected`, so a counter, a cooldown and a report
    could not tell a typo in a lot size from an order the venue may hold.
    """
    PENDING = 'pending'          # Submitted and not ended — in flight, or resting at the venue
    EXECUTED = 'executed'        # Filled
    DENIED = 'denied'            # Refused here and never sent: the order guard, the lot size, the funds at submission, a missing position, a close withheld
    REJECTED = 'rejected'        # Refused by the venue — the simulated one included: its funds or margin check at the fill, the stress test
    CANCELLED = 'cancelled'      # The cancel took effect; `initiator` and `end_reason` say who and why
    EXPIRED = 'expired'          # Ran out without filling — the end of the data, or the venue's own expiry
    UNDELIVERED = 'undelivered'  # The venue confirms it never received the order
    UNACCOUNTED = 'unaccounted'  # We stopped asking: the venue may still hold it, filled or not


# The endings an order did not plan for: refused here or by the venue, never received, or
# unaccounted for. A cancel and an expiry are ordinary ends; these are the ones a reader acts
# on, and a report lists them together as failed orders (#362).
FAILED_ORDER_STATUSES = frozenset({
    OrderStatus.DENIED, OrderStatus.REJECTED, OrderStatus.UNDELIVERED, OrderStatus.UNACCOUNTED,
})


class OrderInitiator(Enum):
    """
    Who ended an order that did not fill — on a cancelled, expired or unaccounted row.

    A cancel the strategy asked for and one the framework sent at a timeout look the same at the
    venue, and a strategy reading its own history has to tell them apart.
    """
    STRATEGY = 'strategy'        # The decision logic asked for the cancel
    FRAMEWORK = 'framework'      # We ended it: a timeout, the end of the run, a protective order released
    VENUE = 'venue'              # The venue ended it without being asked


class OrderEndReason(Enum):
    """Why an order ended without filling — beside `initiator` on the same row."""
    CANCEL_REQUESTED = 'cancel_requested'        # The strategy asked for the cancel
    PROTECTION_RELEASED = 'protection_released'  # A protective order the position no longer needs at the venue: its close is going out, or its stop was withdrawn
    ORDER_TIMEOUT = 'order_timeout'              # No fill within the order timeout
    RESOLUTION_CEILING = 'resolution_ceiling'    # Its answer was lost, and the venue never named it however long we asked
    SESSION_END = 'session_end'                  # The live session ended with the order still out
    SCENARIO_END = 'scenario_end'                # The backtest's data ended with the order still out
    VENUE_CANCELLED = 'venue_cancelled'          # The venue cancelled it on its own
    VENUE_EXPIRED = 'venue_expired'              # The venue let it expire


class FillType(Enum):
    """
    How an order was filled — stored in OrderResult.metadata['fill_type'].

    Determines fill semantics:
        MARKET: Standard market fill at current tick price (taker)
        LIMIT: Limit order filled when price reached its level — it RESTED, so it provided
            liquidity (maker). This is the only maker fill.
        LIMIT_IMMEDIATE: Limit filled immediately after latency, price already past the limit
            — it crossed the book on arrival (TAKER). This line used to say "maker fee",
            which is what made the wrong classification look deliberate; measured against
            Kraken on 2026-09-08, such a fill really is charged the taker rate (#244).
        STOP: Stop trigger reached → filled at current market price (taker)
        STOP_LIMIT: Stop trigger reached and its limit already crossed → fills at once
            (TAKER, for the same reason as LIMIT_IMMEDIATE)

    Note: No STOP_IMMEDIATE — if stop price is already exceeded after latency,
    the order fills immediately at current market price (same as STOP).
    """
    MARKET = 'market'
    LIMIT = 'limit'
    LIMIT_IMMEDIATE = 'limit_immediate'
    STOP = 'stop'
    STOP_LIMIT = 'stop_limit'


class RejectionReason(Enum):
    """
    Why an order was refused — on a denied row (refused here) or a rejected one (by the venue).

    The status says WHO refused, this says why; the two are separate fields because the same
    reason — insufficient funds — can come from our own check at submission or from the venue.
    """
    INSUFFICIENT_MARGIN = 'insufficient_margin'
    INSUFFICIENT_FUNDS = 'insufficient_funds'
    INVALID_LOT_SIZE = 'invalid_lot_size'
    SYMBOL_NOT_TRADEABLE = 'symbol_not_tradeable'
    MARKET_CLOSED = 'market_closed'
    INVALID_PRICE = 'invalid_price'
    ORDER_TYPE_NOT_SUPPORTED = 'order_type_not_supported'
    BROKER_ERROR = 'broker_error'
    REJECTION_COOLDOWN = 'rejection_cooldown'
    STALE_MARKET_DATA = 'stale_market_data'
    # #487 — we asked the venue as long as we said we would and it never named an order we
    # sent, neither open nor closed. It may be resting there. New ENTRIES stop while that
    # is true; closing and protecting what is already held do not.
    UNACCOUNTED_ORDER = 'unaccounted_order'
    # A close for a position this bot does not hold. The simulation's venue also refuses a
    # close whose position was closed while the close was on its way.
    POSITION_NOT_FOUND = 'position_not_found'
    # #503 — the close was held back because the protective order resting over the position
    # could not be cancelled first, and a close beside a working stop can fill twice.
    CLOSE_WITHHELD = 'close_withheld'
    # #507 — the requested size is fine, the LEFTOVER is not: closing it would strand a
    # remainder below the symbol's volume_min, which can never be sold afterwards. Its own
    # reason rather than INVALID_LOT_SIZE, because the lots asked for are valid and a caller
    # reading that name would look in the wrong place.
    REMAINDER_BELOW_MINIMUM = 'remainder_below_minimum'



class ProtectiveLevelEnforcement(Enum):
    """
    Who actually enforces a stop_loss / take_profit declared on an order (#500).

    The level used to be recorded on the Position and enforced by nobody: the payload
    carried no field for it and the engine's check skipped every non-simulation executor,
    on the assumption that the venue had taken it. The assumption was never true, so the
    console showed a stop that did not exist. This says who holds it.

    LOCAL: our own process evaluates it against the tick stream and closes the position
        when it is breached. Protects while we are running and connected; a process that
        dies leaves the position unprotected.
    VENUE: the level rests at the broker as an order of its own and survives our process
        dying. Answered since #503, where Kraken Spot holds a declared stop as a standalone
        STOP order — opt-in, default OFF. It is the STOP only: Kraken has neither OCO nor a
        bracket, so one order rests and it is the one that bounds the loss; a declared take
        profit stays with the local check whatever this says. The MARGIN side is unwalked —
        MT5 holds the level ON the position, with no separate order to derive an answer
        from, so a three-way reading may be needed there (#209).
    """
    LOCAL = 'local'
    VENUE = 'venue'

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

    # Venue-held protective orders (#503)
    # venue_held_protective_orders: the venue accepts a STANDALONE order that can hold a
    # protective level for an open position, so the level survives our process dying.
    # Kraken: True — measured, a stop-loss order rests over a spot holding and fires
    # without us. MT5: False — MT5 holds the level ON the position, a different mechanism
    # (#209). It is NOT native_position_sl_tp, which says who performs a MODIFY: on the
    # two venues that exist the two answers point in opposite directions, and reading one
    # for the other reinstates #500's original defect one level up.
    venue_held_protective_orders: bool = False

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
    comment: str = ''

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

    Extended feature (Tier 2) — a venue declares it on OrderCapabilities.stop_orders
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

    rejection_reason: Optional[RejectionReason] = None
    rejection_message: str = ''

    position_id: Optional[str] = None

    # First-class action discriminator (#330). Distinguishes open and close
    # OrderResults that share the same order_id (= position_id). The
    # EventStreamWriter routes ORDER_SUBMIT vs CLOSE_SUBMIT based on this.
    # A rejection carries it too: a close can be refused as well as an open, and an
    # empty side hid which one it was. None only where a constructor has not set it.
    action: Optional[OrderAction] = None

    # Order dimensions promoted from the metadata bag (#343) — typed,
    # consistently present on PENDING/EXECUTED results, and on rejections as far as
    # the refused order knew them (create_rejection_result makes every caller say).
    # A rejection without its symbol was dropped by every symbol filter.
    # direction: the position direction the order refers to (open: requested
    #   direction; close: direction of the position being closed).
    # requested_lots: lots the algo asked for (vs executed_lots = filled).
    # close_type: full/partial — close-side only, set once filled.
    symbol: Optional[str] = None
    direction: Optional[OrderDirection] = None
    requested_lots: Optional[float] = None
    close_type: Optional[CloseType] = None

    # The order type the algo ASKED for, on every result a refusal included: a refusal that
    # says why but not what was refused cannot be read. A stop-limit stays `stop_limit` after
    # its stop triggered; a close, and a stop-loss or take-profit exit the framework performs,
    # is `market`; an exit through a protective order held at the venue carries that order's
    # type. None only where a constructor has not set it.
    order_type: Optional[OrderType] = None

    # Who ended an order that did not fill, and why — on a cancelled, expired or unaccounted
    # row; None on every other status.
    initiator: Optional[OrderInitiator] = None
    end_reason: Optional[OrderEndReason] = None

    # Submission slippage audit (#340) — algo's trade-channel mid price at
    # the submission moment, propagated from PendingOrder. Surfaced in the
    # event-stream CSV (ORDER_SUBMIT / CLOSE_SUBMIT rows) so downstream
    # analysis can compute the per-fill slippage delta without rejoining
    # against the live audit pipeline. Empty for pre-tick rejections.
    submission: SubmissionMetadata = field(default_factory=SubmissionMetadata)

    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status is OrderStatus.EXECUTED

    @property
    def is_refused(self) -> bool:
        """
        Whether the order was refused — here (denied) or by the venue (rejected).

        The question a caller usually means by "did it go through". Ask the status itself
        where it matters who refused it.

        Returns:
            True for a denied or rejected result
        """
        return self.status in (OrderStatus.DENIED, OrderStatus.REJECTED)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        return {
            'order_id': self.order_id,
            'status': self.status.value,

            'executed_price': self.executed_price,
            'executed_lots': self.executed_lots,
            'execution_time': self.execution_time.isoformat() if self.execution_time else None,

            'commission': self.commission,

            'rejection_reason': self.rejection_reason.value if self.rejection_reason else None,
            'rejection_message': self.rejection_message,

            'position_id': self.position_id,

            'action': self.action.value if self.action else None,
            'symbol': self.symbol,
            'direction': self.direction.value if self.direction else None,
            'requested_lots': self.requested_lots,
            'close_type': self.close_type.value if self.close_type else None,
            'order_type': self.order_type.value if self.order_type else None,
            'initiator': self.initiator.value if self.initiator else None,
            'end_reason': self.end_reason.value if self.end_reason else None,

            'metadata': serialize_value(self.metadata),
        }


# ============================================
# Helper Functions
# ============================================

def create_refusal_result(
    order_id: str,
    reason: RejectionReason,
    message: str = '',
    *,
    status: OrderStatus,
    execution_time: datetime,
    action: OrderAction,
    symbol: Optional[str],
    direction: Optional[OrderDirection],
    requested_lots: Optional[float],
    order_type: Optional[OrderType],
) -> OrderResult:
    """
    Create a standardized refusal result that states who refused what, and when.

    The order's dimensions are keyword-only and have no default on purpose: a caller
    cannot forget one, and where a value is genuinely unknown (a close for a position
    that does not exist has no symbol) it says None explicitly. The same holds for the
    status — whether WE refused it or the venue did is the one thing a reader of the
    refusal acts on, so no caller gets it by default. The time is the canonical clock at
    the refusal — the event stream orders records by it.

    Args:
        order_id: The refused order's id
        reason: Why it was refused
        message: The human sentence beside the reason
        status: DENIED when it was refused here and never sent, REJECTED when the venue
            refused it — the simulated venue included
        execution_time: When it was refused, on the canonical clock
        action: Which side was refused — an open or a close
        symbol: The order's symbol, or None when the refused order had none
        direction: Open: the requested direction; close: the direction of the position
        requested_lots: The lots asked for, or None when unknown (a close of everything)
        order_type: The type the refused order was asked as (a close is a market order)

    Returns:
        OrderResult with status DENIED or REJECTED
    """
    if status not in (OrderStatus.DENIED, OrderStatus.REJECTED):
        raise ValueError(f'A refusal is denied or rejected, not {status.value}')
    return OrderResult(
        order_id=order_id,
        status=status,
        execution_time=execution_time,
        rejection_reason=reason,
        rejection_message=message,
        action=action,
        symbol=symbol,
        direction=direction,
        requested_lots=requested_lots,
        order_type=order_type,
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
        venue_held_protection: Whether the resulting position's protective level should
            rest at the venue as an order of its own (#503). None follows the profile's
            execution.venue_held_protection; True/False overrides it for this order
    """
    symbol: str
    order_type: OrderType
    direction: OrderDirection
    lots: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = ''
    venue_held_protection: Optional[bool] = None


# ============================================
# Modification Result Types
# ============================================

class ModificationRejectionReason(Enum):
    """
    Reason why a position, limit order, or stop order modification was rejected.

    Used by modify_position(), modify_limit_order(), and modify_stop_order()
    to provide structured rejection feedback.
    """
    POSITION_NOT_FOUND = 'position_not_found'
    LIMIT_ORDER_NOT_FOUND = 'limit_order_not_found'
    STOP_ORDER_NOT_FOUND = 'stop_order_not_found'
    INVALID_SL_LEVEL = 'invalid_sl_level'
    INVALID_TP_LEVEL = 'invalid_tp_level'
    SL_TP_CROSS = 'sl_tp_cross'
    INVALID_PRICE = 'invalid_price'
    NO_CURRENT_PRICE = 'no_current_price'
    # #318 — Async modify/cancel reject cases
    ORDER_NOT_CONFIRMED = 'order_not_confirmed'   # broker_ref still None (Option A)
    OPERATION_BUSY = 'operation_busy'             # another in_flight_operation already
    ORDER_TYPE_NOT_SUPPORTED = 'order_type_not_supported'   # capability gate (e.g. stop_orders=False)


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
    PENDING = 'pending'
    SUCCESS = 'success'
    REJECTED = 'rejected'


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
