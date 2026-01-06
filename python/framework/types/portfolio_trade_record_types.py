"""
FiniexTestingIDE - Portfolio Trade Record Types
Types for detailed trade history with full audit trail for pen & paper verification.

Architecture:
- CloseType: Enum for full vs partial position close
- TradeRecord: Flat, serializable record of completed trade with all calculation inputs
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class CloseType(Enum):
    """Type of position close for trade record."""
    FULL = "full"
    PARTIAL = "partial"


@dataclass
class TradeRecord:
    """
    Completed trade record with full audit trail.

    Contains all values needed for manual P&L verification:
    - Entry/exit prices and tick values
    - Symbol properties (digits, contract_size)
    - Fee breakdown
    - Gross and net P&L

    Serializable for process handover and export.
    """
    # === Identity ===
    position_id: str
    symbol: str
    direction: str  # "LONG" / "SHORT" (string for serialization)
    lots: float
    close_type: CloseType

    # === Entry Data ===
    entry_price: float
    entry_time: datetime
    entry_tick_value: float
    entry_bid: float
    entry_ask: float

    # === Exit Data ===
    exit_price: float
    exit_time: datetime
    exit_tick_value: float

    # === Tick Index (for backtesting analysis) ===
    entry_tick_index: int  # tick_counter at position open
    exit_tick_index: int   # tick_counter at position close

    # === Symbol Properties (for formula verification) ===
    digits: int
    contract_size: int

    # === Fees (itemized) ===
    spread_cost: float
    commission_cost: float
    swap_cost: float
    total_fees: float

    # === P&L ===
    gross_pnl: float  # Before fees
    net_pnl: float    # After fees (final result)

    # === Optional Metadata ===
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = ""
    magic_number: int = 0

    # === Account Context ===
    account_currency: str = ""
