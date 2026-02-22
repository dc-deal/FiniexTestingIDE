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

from python.framework.types.order_types import OrderDirection


class CloseType(Enum):
    """Type of position close for trade record."""
    FULL = "full"
    PARTIAL = "partial"


class CloseReason(Enum):
    """
    Reason why a position was closed — stored on TradeRecord.close_reason.

    MANUAL: Algo/strategy close (no specific trigger)
    SL_TRIGGERED: Stop-loss price level hit
    TP_TRIGGERED: Take-profit price level hit
    SCENARIO_END: Position auto-closed at end of simulation
    """
    MANUAL = ""
    SL_TRIGGERED = "sl_triggered"
    TP_TRIGGERED = "tp_triggered"
    SCENARIO_END = "scenario_end"


class EntryType(Enum):
    """
    How a position was opened — stored on TradeRecord.entry_type.

    Used for:
    - Audit trail: which order type opened this position
    - Fee determination: LIMIT/STOP_LIMIT → maker fee, MARKET/STOP → taker fee

    Values:
        MARKET: Opened via market order (fill at current price)
        LIMIT: Opened via limit order (fill at limit price or better)
        STOP: Opened via stop order (trigger price reached → market fill)
        STOP_LIMIT: Opened via stop-limit order (trigger → limit fill)
    """
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


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
    direction: OrderDirection
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

    # === Close Reason ===
    close_reason: CloseReason = CloseReason.MANUAL

    # === Entry Type ===
    entry_type: EntryType = EntryType.MARKET

    # === Account Context ===
    account_currency: str = ""
