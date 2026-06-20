"""
FiniexTestingIDE - Live Telemetry Core Snapshot
Shared core of the live-telemetry frames (simulation batch + live session).
"""

from dataclasses import dataclass
from typing import Optional

from python.framework.types.decision_logic_types import DecisionAwareness


@dataclass
class LiveCoreSnapshot:
    """
    The subset both live-telemetry frames share.

    Composed by LiveScenarioStats (simulation batch) and AutoTraderDisplayStats
    (live session) so the common identity + portfolio basics live in one place
    and serialize identically for the viewer.

    Args:
        symbol: Trading symbol
        ticks_processed: Total ticks processed so far
        balance: Current account balance
        initial_balance: Starting account balance
        total_trades: Total completed trades
        winning_trades: Number of winning trades
        losing_trades: Number of losing trades
        last_awareness: Ephemeral narration from the decision logic (None until emitted)
    """
    symbol: str
    ticks_processed: int = 0
    balance: float = 0.0
    initial_balance: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    last_awareness: Optional[DecisionAwareness] = None
