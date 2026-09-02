"""
FiniexTestingIDE - Reconciliation Types (#151)
Domain types for the live reconciliation layer: broker truth-pull results
(BrokerPosition / BrokerOrder), the per-cycle ReconciliationResult bucket set,
and the one-time flat-preflight result.

Live-only — simulation's PortfolioManager IS the truth, so reconciliation does
not apply there. Position buckets are populated on MARGIN adapters only; on SPOT
the broker has no position object (holdings are balances) and the position diff
is skipped.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderType,
)


@dataclass
class BrokerPosition:
    """
    Broker-reported open position (MARGIN only — empty on spot).

    Args:
        symbol: Trading symbol
        direction: Position direction (LONG/SHORT)
        lots: Open size
        entry_price: Broker-reported entry price
        broker_ref: Broker-side reference (primary join key against local Position)
        unrealized_pnl: Broker-reported unrealized P&L, if provided
        margin_used: Broker-reported margin consumed, if provided
        raw: Untouched broker payload for forensic inspection
    """
    symbol: str
    direction: OrderDirection
    lots: float
    entry_price: float
    broker_ref: Optional[str] = None
    unrealized_pnl: Optional[float] = None
    margin_used: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class BrokerOrder:
    """
    Broker-reported open (resting) order — world-agnostic.

    Args:
        broker_ref: Broker-side order reference (primary join key against local pending)
        symbol: Trading symbol
        direction: Order direction (LONG/SHORT)
        order_type: Order type (LIMIT, etc.)
        lots: Order size
        status: Broker-side order status
        price: Limit price, if applicable
        stop_loss: Attached stop-loss, if provided
        take_profit: Attached take-profit, if provided
        client_order_id: The key WE chose for this order, echoed back by the venue (#473).
            None when the venue reports no key — which is itself the fact that tells an
            order we placed apart from one somebody else did
        filled_lots: How much of `lots` the venue has already executed. `lots` is the ORIGINAL
            order size, not the unfilled remainder — Kraken reports `vol` and `vol_exec`
            separately — so without this a partially filled resting order looks untouched, and
            boot adoption (#355) would rebuild it at full size
        raw: Untouched broker payload for forensic inspection
    """
    broker_ref: str
    symbol: str
    direction: OrderDirection
    order_type: OrderType
    lots: float
    status: BrokerOrderStatus
    price: Optional[float] = None
    filled_lots: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    client_order_id: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class ReconciliationResult:
    """
    Outcome of one reconcile() cycle — divergence buckets + clean flag.

    Position buckets are populated on MARGIN only; order buckets are populated in
    both worlds. ghost_* = broker has it, we lack it locally; orphan_* = we have it
    locally, broker lacks it; stale_* = matched by broker_ref but field mismatch.

    A resting broker order that WE placed is no longer a ghost (#355). The client order
    id says whose it is, so an unmatched broker order splits three ways — our own
    session's, an earlier session's, or nobody's we can name — and the first of those
    splits again depending on whether a local pending is still waiting for it:

        our key + local pending without broker_ref  → attributed  (the lost answer)
        our key, this session, no local pending     → abandoned   (placed and forgotten)
        our key shape, another session              → foreign_session (adoption, Phase 2)
        no key of OURS                              → ghost       (not ours, as far as we know)

    Args:
        timestamp: When the reconcile cycle completed (UTC, tz-aware)
        ghost_positions: Broker positions with no local Position match (MARGIN)
        orphan_positions: Local positions with no broker match (MARGIN)
        stale_positions: (local, broker) pairs matched but diverging (MARGIN)
        ghost_orders: Broker orders with no local match and no client order id OF OURS —
            either none at all, or one that is not our shape (another client's scheme).
            Placed by hand or by somebody else, as far as we can tell
        orphan_orders: Local pendings (with broker_ref) with no broker match
        stale_orders: (local, broker) order pairs matched but diverging
        attributed_orders: (local, broker) pairs matched by THIS session's client order
            id where the local pending has no broker_ref yet — a submit whose answer was
            lost (#473) and which the venue does hold. Not a divergence: the reference is
            restored by the executor, which returns the order to the poll path
        abandoned_orders: Broker orders carrying this session's client order id with no
            local pending left — we placed them and no longer track them. An order the
            latency queue is still waiting on (MARKET / CLOSE) is NOT in here: it has no
            counterpart among the resting orders the diff compares, but it is tracked
        foreign_session_orders: Broker orders carrying a client order id of our shape but
            another session's discriminator — an earlier session of this bot. Boot-time
            adoption is #355 Phase 2; mid-session they are a divergence
        unconfirmed_orders: Local pendings whose submit was never answered and which the
            venue does not show either. They block has_pending_orders() and nothing times
            them out, so each one is reported once into the session error pot; resolving
            them needs a targeted status query (#487)
        partial_fills: Local pendings observed as partially filled (#326 cumulative_*)
        is_clean: True when every DIVERGENCE bucket is empty — attributed_orders and
            partial_fills do not count, being a repair and a normal market outcome
        skipped_reason: Set when broker truth could not be pulled at all (#473) — the
            cycle produced no comparison, which is neither clean nor divergent. Named
            rather than boolean because "unreachable" is the fact the operator needs
    """
    timestamp: datetime
    ghost_positions: List[BrokerPosition] = field(default_factory=list)
    orphan_positions: List[Position] = field(default_factory=list)
    stale_positions: List[Tuple[Position, BrokerPosition]] = field(default_factory=list)
    ghost_orders: List[BrokerOrder] = field(default_factory=list)
    orphan_orders: List[PendingOrder] = field(default_factory=list)
    stale_orders: List[Tuple[PendingOrder, BrokerOrder]] = field(default_factory=list)
    attributed_orders: List[Tuple[PendingOrder, BrokerOrder]] = field(default_factory=list)
    abandoned_orders: List[BrokerOrder] = field(default_factory=list)
    foreign_session_orders: List[BrokerOrder] = field(default_factory=list)
    unconfirmed_orders: List[PendingOrder] = field(default_factory=list)
    partial_fills: List[PendingOrder] = field(default_factory=list)
    is_clean: bool = True
    skipped_reason: Optional[str] = None


@dataclass
class FlatCheckResult:
    """
    Outcome of the one-time flat-preflight (consumed by the Field Study #332).

    On spot, flat means: no resting broker orders AND no asset balance beyond the
    quote currency (above the dust threshold).

    Args:
        is_flat: True when the account is flat
        open_orders: Resting broker orders blocking the flat state
        asset_balances: Non-quote asset balances above the dust threshold
        reasons: Human-readable blocking reasons (empty when flat)
    """
    is_flat: bool
    open_orders: List[BrokerOrder] = field(default_factory=list)
    asset_balances: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
