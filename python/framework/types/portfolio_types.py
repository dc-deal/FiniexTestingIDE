from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


from python.framework.trading_env.abstract_trading_fee import AbstractTradingFee
from python.framework.types.broker_types import FeeType
from python.framework.types.order_types import OrderDirection
from python.framework.types.portfolio_trade_record_types import EntryType


class PositionStatus(Enum):
    """Position status"""
    OPEN = "open"
    CLOSED = "closed"
    PARTIALLY_CLOSED = "partially_closed"


@dataclass
class Position:
    """
    Open trading position with full fee tracking.

    Now includes List[AbstractTradingFee] for all costs.
    """
    position_id: str

    symbol: str
    direction: OrderDirection
    lots: float
    entry_price: float
    entry_time: datetime

    # Optional SL/TP
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    # Entry type (market or limit)
    entry_type: EntryType = EntryType.MARKET

    # Fee objects (polymorphic)
    fees: List[AbstractTradingFee] = field(default_factory=list)

    # Current state
    current_price: float = 0.0
    unrealized_pnl: float = 0.0

    # Status
    status: PositionStatus = PositionStatus.OPEN

    # Metadata
    comment: str = ""
    close_time: Optional[datetime] = None
    close_price: Optional[float] = None

    # === Trade Record Fields (for P&L verification) ===
    entry_tick_value: float = 0.0
    entry_bid: float = 0.0
    entry_ask: float = 0.0
    exit_tick_value: float = 0.0
    digits: int = 5
    contract_size: int = 100000
    gross_pnl: float = 0.0

    # === Tick Index (for backtesting analysis) ===
    entry_tick_index: int = 0
    exit_tick_index: int = 0

    def update_current_price(self, bid: float, ask: float, tick_value: float, digits: int) -> None:
        """
        Update current price and recalculate unrealized P&L.

        P&L calculation includes all accumulated fees.
        """
        # Use appropriate price based on position direction
        if self.direction == OrderDirection.LONG:
            self.current_price = bid  # Close at bid
        else:
            self.current_price = ask  # Close at ask

        # Calculate price difference in points
        if self.direction == OrderDirection.LONG:
            price_diff = self.current_price - self.entry_price
        else:
            price_diff = self.entry_price - self.current_price

        # Convert to points
        points = price_diff * (10 ** digits)

        # Calculate P&L: points * tick_value * lots - all fees
        gross_pnl = points * tick_value * self.lots
        total_fees = self.get_total_fees()

        # Store gross_pnl for trade record
        self.gross_pnl = gross_pnl
        self.unrealized_pnl = gross_pnl - total_fees

    def add_fee(self, fee: AbstractTradingFee) -> None:
        """Add fee to position"""
        self.fees.append(fee)

    def get_total_fees(self) -> float:
        """Get sum of all fees attached to this position"""
        return sum(fee.cost for fee in self.fees)

    def get_fees_by_type(self, fee_type) -> List[AbstractTradingFee]:
        """Get all fees of specific type"""
        return [fee for fee in self.fees if fee.fee_type == fee_type]

    def get_spread_cost(self) -> float:
        """Get total spread cost"""
        spread_fees = self.get_fees_by_type(FeeType.SPREAD)
        return sum(fee.cost for fee in spread_fees)

    def get_commission_cost(self) -> float:
        """Get total commission cost"""
        comm_fees = self.get_fees_by_type(FeeType.COMMISSION)
        return sum(fee.cost for fee in comm_fees)

    def get_swap_cost(self) -> float:
        """Get total swap cost"""
        swap_fees = self.get_fees_by_type(FeeType.SWAP)
        return sum(fee.cost for fee in swap_fees)

    def get_margin_used(self, contract_size: float, leverage: int) -> float:
        """Calculate margin used by this position"""
        return (self.lots * contract_size * self.entry_price) / leverage

    # ============================================
    # SL/TP Trigger Detection
    # ============================================

    def is_sl_triggered(self, bid: float, ask: float) -> bool:
        """
        Check if stop loss is triggered at current prices.

        Args:
            bid: Current bid price
            ask: Current ask price

        Returns:
            True if SL level is breached
        """
        if self.stop_loss is None:
            return False
        if self.direction == OrderDirection.LONG:
            return bid <= self.stop_loss  # LONG closes at bid
        return ask >= self.stop_loss  # SHORT closes at ask

    def is_tp_triggered(self, bid: float, ask: float) -> bool:
        """
        Check if take profit is triggered at current prices.

        Args:
            bid: Current bid price
            ask: Current ask price

        Returns:
            True if TP level is breached
        """
        if self.take_profit is None:
            return False
        if self.direction == OrderDirection.LONG:
            return bid >= self.take_profit  # LONG closes at bid
        return ask <= self.take_profit  # SHORT closes at ask

    @property
    def is_open(self) -> bool:
        """Check if position is still open"""
        return self.status == PositionStatus.OPEN
