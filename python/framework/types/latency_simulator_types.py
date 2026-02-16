# ============================================
# python/framework/types/latency_simulator_types.py
# ============================================
"""
FiniexTestingIDE - Pending Order Types
Data structures for pending order tracking across execution modes.

Used by both simulation (OrderLatencySimulator) and live (LiveTradeExecutor).
Mode-specific fields are Optional — each mode uses what it needs:

Simulation fields: placed_at_tick, fill_at_tick
Live fields:       submitted_at, broker_ref, timeout_at
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from python.framework.types.order_types import OrderDirection
from python.framework.utils.process_serialization_utils import serialize_value


class PendingOrderAction(Enum):
    """
    Pending order action type.

    OPEN: New position order
    CLOSE: Close existing position order
    """
    OPEN = "open"
    CLOSE = "close"

    def __str__(self) -> str:
        """String representation returns the enum value"""
        return self.value


@dataclass
class PendingOrder:
    """
    Order waiting to be filled (open or close).

    Shared across execution modes. Contains all information needed
    to execute the order once the fill is confirmed.

    Mode-specific fields:
        Simulation: placed_at_tick, fill_at_tick (tick-based delay)
        Live:       submitted_at, broker_ref, timeout_at (time-based, broker tracking)
    """
    pending_order_id: str

    # Order type identification
    order_action: PendingOrderAction = None  # "open" or "close"

    # === Simulation fields (tick-based delay) ===
    placed_at_tick: Optional[int] = None
    fill_at_tick: Optional[int] = None

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

    def to_dict(self) -> dict:
        return {
            'pending_order_id': self.pending_order_id,
            'order_action': self.order_action.value if self.order_action else None,
            # Simulation
            'placed_at_tick': self.placed_at_tick,
            'fill_at_tick': self.fill_at_tick,
            # Live
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'broker_ref': self.broker_ref,
            # Order details
            'symbol': self.symbol,
            'direction': self.direction.value if self.direction else None,
            'lots': self.lots,
            'order_kwargs': serialize_value(self.order_kwargs),
            'close_lots': self.close_lots,
        }
