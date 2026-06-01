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
        raw: Untouched broker payload for forensic inspection
    """
    broker_ref: str
    symbol: str
    direction: OrderDirection
    order_type: OrderType
    lots: float
    status: BrokerOrderStatus
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class ReconciliationResult:
    """
    Outcome of one reconcile() cycle — divergence buckets + clean flag.

    Position buckets are populated on MARGIN only; order buckets are populated in
    both worlds. ghost_* = broker has it, we lack it locally; orphan_* = we have it
    locally, broker lacks it; stale_* = matched by broker_ref but field mismatch.

    Args:
        timestamp: When the reconcile cycle completed (UTC, tz-aware)
        ghost_positions: Broker positions with no local Position match (MARGIN)
        orphan_positions: Local positions with no broker match (MARGIN)
        stale_positions: (local, broker) pairs matched but diverging (MARGIN)
        ghost_orders: Broker orders with no local pending match
        orphan_orders: Local pendings (with broker_ref) with no broker match
        stale_orders: (local, broker) order pairs matched but diverging
        partial_fills: Local pendings observed as partially filled (#326 cumulative_*)
        is_clean: True when every bucket is empty
    """
    timestamp: datetime
    ghost_positions: List[BrokerPosition] = field(default_factory=list)
    orphan_positions: List[Position] = field(default_factory=list)
    stale_positions: List[Tuple[Position, BrokerPosition]] = field(default_factory=list)
    ghost_orders: List[BrokerOrder] = field(default_factory=list)
    orphan_orders: List[PendingOrder] = field(default_factory=list)
    stale_orders: List[Tuple[PendingOrder, BrokerOrder]] = field(default_factory=list)
    partial_fills: List[PendingOrder] = field(default_factory=list)
    is_clean: bool = True


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
