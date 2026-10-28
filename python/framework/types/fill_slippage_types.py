"""
FiniexTestingIDE - Fill Slippage Types (#631)

How far a fill landed from the price it is measured against, and which price that is. One
derivation serves the order history's rows, the execution statistics and a trade's exit, in both
pipelines, from the order-event stream they share.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SlippageReference(Enum):
    """What a fill's slippage is measured against."""
    SUBMISSION_MID = 'submission_mid'       # a market order: the mid when it was sent
    LIMIT_PRICE = 'limit_price'             # a limit order: its limit in force at the fill
    TRIGGER_PRICE = 'trigger_price'         # a stop or stop-limit: its trigger in force
    PROTECTIVE_LEVEL = 'protective_level'   # a stop-loss or take-profit close: the level that fired


@dataclass(frozen=True)
class FillSlippage:
    """
    One fill's slippage (#631).

    Args:
        measured_against: What it is measured against
        reference_price: That price; None when it was not captured
        slippage: Fill against reference, signed so that a positive number is a cost; None when
            the reference or the side was not captured
        slippage_pct: The same in percent of the reference
    """
    measured_against: SlippageReference
    reference_price: Optional[float]
    slippage: Optional[float]
    slippage_pct: Optional[float]
