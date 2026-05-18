# ============================================
# python/framework/types/latency_simulator_types.py
# ============================================
"""
FiniexTestingIDE - Pending Order Types
Data structures for pending order tracking across execution modes.

Used by both simulation (OrderLatencySimulator) and live (LiveTradeExecutor).
Mode-specific fields are Optional — each mode uses what it needs:

Simulation fields: placed_at_msc, broker_fill_msc (millisecond timestamps)
Live fields:       submitted_at, broker_ref, timeout_at
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Dict, List, Optional

from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.utils.process_serialization_utils import serialize_value


class PendingOrderAction(StrEnum):
    """
    Pending order action type.

    OPEN: New position order
    CLOSE: Close existing position order
    """
    OPEN = "open"
    CLOSE = "close"


class PendingOrderOutcome(StrEnum):
    """
    How a pending order's lifecycle ended.

    FILLED: Normal fill after latency delay (simulation) or broker confirmation (live)
    REJECTED: Rejected after pending phase (stress test, broker rejection)
    TIMED_OUT: Broker did not respond within timeout threshold (live only)
    FORCE_CLOSED: Forcefully resolved at scenario end (orders still in queue)
    """
    FILLED = "filled"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    FORCE_CLOSED = "force_closed"


class PendingOperation(StrEnum):
    """
    In-flight broker-side operation on a pending order.

    Used by #318's async modify/cancel pattern to track whether an
    operation is currently dispatched and awaiting resolution. The
    sim and live executors set this when a modify/cancel is enqueued
    and clear it when the resolve path (next-tick for sim, drain_inbox
    for live) applies the change.

    NONE: No operation in flight; order in steady state
    PENDING_SUBMIT: Initial submit not yet broker-confirmed (broker_ref=None)
    PENDING_MODIFY: Modify dispatched, awaiting next-tick resolve / drain
    PENDING_CANCEL: Cancel dispatched, awaiting next-tick resolve / drain
    """
    NONE = "none"
    PENDING_SUBMIT = "submit"
    PENDING_MODIFY = "modify"
    PENDING_CANCEL = "cancel"


@dataclass
class ModificationRequest:
    """
    Provisional values held during a PENDING_MODIFY operation.

    The request is attached to the PendingOrder when the modify is
    enqueued. On successful resolve, the values are applied to the
    PendingOrder; on rejection, the request is discarded (no state
    change). UNSET-translation happens at the executor boundary —
    fields here are concrete None or a value.

    Args:
        new_price: New limit price (LIMIT) or new stop trigger price (STOP /
                   STOP_LIMIT). Maps to PendingOrder.entry_price. None = no
                   price change.
        new_limit_price: New limit price for STOP_LIMIT only (the limit price
                         that activates once stop trigger fires). Maps to
                         PendingOrder.order_kwargs['limit_price']. None = no
                         change. Unused for LIMIT (new_price covers it) and
                         position-modifies (no limit-price concept).
        new_stop_loss: New SL level (None = explicit clear, absent in kwargs = no change)
        new_take_profit: New TP level (analog)
        submitted_at: Timestamp the modify was enqueued (UTC, live only)
        apply_at_msc: Simulation resolution trigger (msc when resolve fires)
    """
    new_price: Optional[float] = None
    new_limit_price: Optional[float] = None
    new_stop_loss: Optional[float] = None
    new_take_profit: Optional[float] = None
    submitted_at: Optional[datetime] = None
    apply_at_msc: Optional[int] = None


@dataclass
class PendingOrder:
    """
    Order waiting to be filled (open or close).

    Shared across execution modes. Contains all information needed
    to execute the order once the fill is confirmed.

    Mode-specific fields:
        Simulation: placed_at_msc, broker_fill_msc (ms-timestamp-based delay)
        Live:       submitted_at, broker_ref, timeout_at (time-based, broker tracking)
    """
    pending_order_id: str

    # Order type identification
    order_action: PendingOrderAction = None  # "open" or "close"
    order_type: Optional[OrderType] = None   # MARKET or LIMIT

    # === Simulation fields (ms-timestamp-based delay) ===
    placed_at_msc: Optional[int] = None
    broker_fill_msc: Optional[int] = None

    # === Live fields (broker tracking) ===
    submitted_at: Optional[datetime] = None
    broker_ref: Optional[str] = None
    timeout_at: Optional[datetime] = None

    # === For OPEN orders ===
    symbol: Optional[str] = None
    direction: Optional[OrderDirection] = None
    lots: Optional[float] = None
    entry_price: Optional[float] = None
    entry_time: Optional[datetime] = None
    order_kwargs: Optional[Dict] = None

    # === For CLOSE orders ===
    close_lots: Optional[float] = None

    # === Async modify/cancel state (#318) ===
    # in_flight_operation: NONE in steady state, or PENDING_SUBMIT/MODIFY/CANCEL
    #   while a broker-side operation is dispatched and awaiting resolve.
    # pending_modification: provisional values during PENDING_MODIFY (applied
    #   on resolve, discarded on reject).
    # cancel_apply_at_msc: sim resolution trigger for PENDING_CANCEL (live
    #   uses drain_inbox instead, so this field is sim-only).
    in_flight_operation: PendingOperation = PendingOperation.NONE
    pending_modification: Optional[ModificationRequest] = None
    cancel_apply_at_msc: Optional[int] = None

    # === Order ↔ Executions pairing (#326) ===
    # trades: per-execution BrokerTrade records produced by this order.
    #   Single fill orders typically produce 1 record; partial fills produce N.
    #   Live: populated by _handle_trades_response after FILLED detection.
    #   Sim: populated by TradeSimulator._fill_open_order on synthetic fill.
    # cumulative_*: derived aggregates over trades, cached on append_trade.
    trades: List[BrokerTrade] = field(default_factory=list)
    cumulative_filled_lots: float = 0.0
    cumulative_fee: float = 0.0
    cumulative_avg_price: float = 0.0

    def append_trade(self, trade: BrokerTrade) -> None:
        """
        Append an execution record and recompute cumulative aggregates.

        Args:
            trade: BrokerTrade to append. Mutates self.trades and the
                   cumulative_* fields. Idempotent on identical inputs at
                   the caller — no duplicate detection here.
        """
        self.trades.append(trade)
        self.cumulative_filled_lots = sum(t.volume for t in self.trades)
        self.cumulative_fee = sum(t.fee for t in self.trades)
        if self.cumulative_filled_lots > 0:
            volume_weighted_sum = sum(t.volume * t.price for t in self.trades)
            self.cumulative_avg_price = volume_weighted_sum / self.cumulative_filled_lots

    def to_dict(self) -> dict:
        return {
            'pending_order_id': self.pending_order_id,
            'order_action': self.order_action.value if self.order_action else None,
            # Simulation
            'placed_at_msc': self.placed_at_msc,
            'broker_fill_msc': self.broker_fill_msc,
            # Live
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'broker_ref': self.broker_ref,
            # Order details
            'symbol': self.symbol,
            'direction': self.direction.value if self.direction else None,
            'lots': self.lots,
            'order_kwargs': serialize_value(self.order_kwargs),
            'close_lots': self.close_lots,
            # Async operation state (#318)
            'in_flight_operation': self.in_flight_operation.value if self.in_flight_operation else None,
            # Trade records (#326)
            'trades': [t.to_dict() for t in self.trades],
            'cumulative_filled_lots': self.cumulative_filled_lots,
            'cumulative_fee': self.cumulative_fee,
            'cumulative_avg_price': self.cumulative_avg_price,
        }
