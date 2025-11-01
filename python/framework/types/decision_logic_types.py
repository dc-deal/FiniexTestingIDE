"""
FiniexTestingIDE - Decision Logic Types (Refactored)
Type definitions for decision logic layer

All decision logic implementations must use these typed structures.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict


class DecisionLogicAction(Enum):
    """
    Trading action signals from DecisionLogic.

    These are decision-level signals that MAY be converted to orders.
    Not all actions result in orders (e.g. FLAT = no action).

    Currently implemented:
    - BUY: Open long position
    - SELL: Open short position
    - FLAT: No action / close position

    Future actions (not yet implemented):
    - LIMITBUY: Buy with limit order
    - LIMITSELL: Sell with limit order
    - REDUCE: Reduce position size
    - CLOSE: Explicit close signal
    """
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"

    def __str__(self) -> str:
        """String representation returns the enum value"""
        return self.value


@dataclass
class DecisionLogicStatistics:
    """
    Statistics tracking for DecisionLogic performance.

    Tracks decision-making and order execution metrics.
    Replaces dict-based statistics for type safety.
    """
    decisions_made: int = 0       # Total decisions generated
    buy_signals: int = 0          # BUY actions generated
    sell_signals: int = 0         # SELL actions generated
    flat_signals: int = 0         # FLAT actions generated
    orders_executed: int = 0      # Orders successfully executed
    orders_rejected: int = 0      # Orders rejected by broker


@dataclass
class Decision:
    """
    Trading decision output from DecisionLogic.

    Structured output that replaces dict-based decision format.
    DecisionLogic.compute() returns this to orchestrator.

    CHANGE: action is now DecisionLogicAction enum instead of str.
    """
    action: DecisionLogicAction       # BUY, SELL, FLAT (enum)
    confidence: float                 # 0.0 - 1.0
    reason: str = ""                  # Human-readable explanation
    price: float = 0.0                # Price at decision time
    timestamp: str = ""               # ISO format UTC timestamp
    metadata: Dict[str, Any] = field(default_factory=dict)  # Additional data

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict with enum as string value for JSON serialization"""
        return {
            "action": self.action.value,  # Enum -> string
            "confidence": self.confidence,
            "reason": self.reason,
            "price": self.price,
            "timestamp": self.timestamp,
            "metadata": self.metadata
        }
