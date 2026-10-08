"""
FiniexTestingIDE - Active Orders Snapshot Types

What a unit's executor holds at one moment: the orders resting at the venue (or the trade
simulator) and how many are still on their way to it. Read by the live display and by the
pending-orders report, which takes it at the unit's end.

How orders LEFT their in-flight phase is not counted here: the report derives that from the
order-event stream (#362), so it cannot disagree with the record it summarises.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType


@dataclass
class ActiveOrderSnapshot:
    """
    Snapshot of a single active order (limit or stop) for stats reporting.

    Populated at stats collection time from _active_limit_orders / _active_stop_orders.
    Contains the order_id that Decision Logic received from send_order() — same ID
    used for modify_limit_order(), modify_stop_order(), cancel_limit_order(), cancel_stop_order().

    Args:
        order_id: Order identifier (same as OrderResult.order_id from send_order())
        order_type: LIMIT, STOP, or STOP_LIMIT
        symbol: Trading symbol
        direction: LONG or SHORT
        lots: Position size
        entry_price: Limit price (LIMIT) or stop trigger price (STOP/STOP_LIMIT)
        limit_price: Limit fill price (only for STOP_LIMIT, None otherwise)
        stop_loss: Stop loss price (from order_kwargs, None if not set)
        take_profit: Take profit price (from order_kwargs, None if not set)
        cumulative_filled_lots: Progressive partial-fill state (#330). V1.3 always
            0.0 because Kraken status parser does not surface PARTIALLY_FILLED yet
            (#342 closes that gap). ORDERS panel BrokerOrder sub-row stays dormant
            until non-zero values arrive from the parser.
        requested_lots: Originally submitted lots (#330). Populated whenever the
            executor builds the snapshot from a PendingOrder; pairs with
            cumulative_filled_lots for "filled / requested" rendering.
        submitted_at: When the order entered pending (live) — for resting-age display.
    """
    order_id: str
    order_type: OrderType
    symbol: str
    direction: OrderDirection
    lots: float
    entry_price: float
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    cumulative_filled_lots: float = 0.0
    requested_lots: float = 0.0
    submitted_at: Optional[datetime] = None


@dataclass
class ActiveOrdersSnapshot:
    """
    The orders a unit holds at one moment.

    Args:
        active_limit_orders: Resting LIMIT orders
        active_stop_orders: Resting STOP and STOP_LIMIT orders, protective orders included
        latency_queue_count: Orders still on their way to the venue
    """
    active_limit_orders: List[ActiveOrderSnapshot] = field(default_factory=list)
    active_stop_orders: List[ActiveOrderSnapshot] = field(default_factory=list)
    latency_queue_count: int = 0
