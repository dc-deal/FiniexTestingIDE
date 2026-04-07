"""
FiniexTestingIDE - Decision Logic Types
Type definitions for decision logic layer

All decision logic implementations must use these typed structures.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict

from python.framework.types.parameter_types import OutputValue


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
