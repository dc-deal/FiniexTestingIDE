"""
FiniexTestingIDE - Decision Logic Types
Type definitions for decision logic layer

All decision logic implementations must use these typed structures.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from python.framework.types.parameter_types import OutputValue


class AwarenessLevel(Enum):
    """
    Visual severity for awareness narration.

    Controls icon + color in both AutoTrader and backtesting displays.
    """
    INFO = 'info'
    NOTICE = 'notice'
    ALERT = 'alert'


@dataclass(frozen=True, slots=True)
class DecisionAwareness:
    """
    Ephemeral narration slot — what the algo is "thinking" right now.

    Single-slot, last-write-wins. NOT persisted to logs or batch_summary.
    Display layers read non-destructively.

    Args:
        message: Human-readable narration (e.g. "RSI 52, no edge")
        level: Visual severity (INFO/NOTICE/ALERT)
        reason_key: Optional machine-readable key for grouping (e.g. 'rsi_filter_buy')
    """
    message: str
    level: AwarenessLevel = AwarenessLevel.INFO
    reason_key: Optional[str] = None


@dataclass(frozen=True, slots=True)
class StrategyEvent:
    """
    A timestamped strategy moment for the event tape ring buffer.

    Elevated log entry: written to the scenario log at INFO level AND
    appended to the in-memory ring buffer for live UI display.

    Args:
        message: Human-readable event description
        level: Algo-semantic severity (INFO/NOTICE/ALERT) — does NOT
               change the underlying log level, which is always INFO
        tick_time: Tick timestamp (sim time in backtests, wall-clock in live)
        reason_key: Optional machine-readable key for grouping/filtering
    """
    message: str
    level: AwarenessLevel = AwarenessLevel.INFO
    tick_time: Optional[datetime] = None
    reason_key: Optional[str] = None


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
class Decision:
    """
    Trading decision — action + typed output parameters.

    DecisionLogic.compute() returns this to orchestrator.
    Output fields are declared via get_output_schema() on the logic.
    """
    action: DecisionLogicAction                                # BUY, SELL, FLAT (enum)
    outputs: Dict[str, OutputValue] = field(default_factory=dict)  # typed via get_output_schema()

    def get_signal(self, name: str) -> OutputValue:
        """Access a decision output value by name.

        Args:
            name: Output parameter name from get_output_schema()

        Returns:
            The output value
        """
        return self.outputs[name]
