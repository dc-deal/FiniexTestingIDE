# ============================================
# python/framework/types/latency_simulator_types.py
# ============================================
"""
FiniexTestingIDE - Latency Simulator Types
Data structures for order delay simulation
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from python.framework.types.order_types import OrderDirection
from python.framework.utils.process_serialization_utils import serialize_value


class PendingOrderAction(Enum):
    """
    Order type classification.

    Common (Tier 1): MARKET, LIMIT
    Extended (Tier 2): STOP, STOP_LIMIT, TRAILING_STOP, ICEBERG
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

    Contains all information needed to execute the order once
    the delay period has elapsed.
    """
    pending_order_id: str
    placed_at_tick: int
    fill_at_tick: int

    # Order type identification
    order_action: PendingOrderAction = None  # "open" or "close"

    # For OPEN orders
    symbol: Optional[str] = None
    direction: Optional[OrderDirection] = None
    lots: Optional[float] = None
    entry_price: Optional[float] = None      # For Position converter
    entry_time: Optional[datetime] = None  # For Position converter
    order_kwargs: Optional[Dict] = None

    # For CLOSE orders
    close_lots: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            'pending_order_id': self.pending_order_id,
            'placed_at_tick': self.placed_at_tick,
            'fill_at_tick': self.fill_at_tick,
            'order_action': self.order_action.value if self.order_action else None,
            'symbol': self.symbol,
            'direction': self.direction.value if self.direction else None,
            'lots': self.lots,
            'order_kwargs': serialize_value(self.order_kwargs),
            'close_lots': self.close_lots,
        }
