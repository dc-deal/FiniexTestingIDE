from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from python.framework.trading_env.trading_fees import AbstractTradingFee, FeeType
from python.framework.types.order_types import OrderDirection


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

    # Fee objects (polymorphic)
    fees: List[AbstractTradingFee] = field(default_factory=list)

    # Current state
    current_price: float = 0.0
    unrealized_pnl: float = 0.0

    # Status
    status: PositionStatus = PositionStatus.OPEN
    pending: bool = False  # a converted pending order

    # Metadata
    comment: str = ""
    magic_number: int = 0
    close_time: Optional[datetime] = None
    close_price: Optional[float] = None

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

    @property
    def is_open(self) -> bool:
        """Check if position is still open"""
        return self.status == PositionStatus.OPEN
