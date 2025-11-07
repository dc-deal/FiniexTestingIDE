# ============================================
# python/framework/types/latency_simulator_types.py
# ============================================
"""
FiniexTestingIDE - Latency Simulator Types
Data structures for order delay simulation
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from python.framework.types.order_types import OrderDirection


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
    order_id: str
    placed_at_tick: int
    fill_at_tick: int

    # Order type identification
    order_action: PendingOrderAction = None  # "open" or "close"

    # For OPEN orders
    symbol: Optional[str] = None
    direction: Optional[OrderDirection] = None
    lots: Optional[float] = None
    order_kwargs: Optional[Dict] = None

    # For CLOSE orders
    position_id: Optional[str] = None
    close_lots: Optional[float] = None
