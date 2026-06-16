from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


from python.framework.trading_env.abstract_trading_fee import AbstractTradingFee
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.broker_types import FeeType
from python.framework.types.trading_env_types.order_types import OrderDirection
from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata
from python.framework.types.portfolio_types.portfolio_trade_record_types import EntryType
from python.framework.utils.trading_math.pnl_math import gross_pnl_from_price_diff


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
    # Immutable after open — tracks initial lot size before partial closes
    original_lots: float
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

    # === External Broker Reference (#330) ===
    # Carried over from PendingOrder.broker_ref at fill time. Empty for sim,
    # populated for live (Kraken txid / MT5 ticket). Consumed by #151 Reconciler.
    broker_ref: Optional[str] = None

    # === Per-Execution Detail (#330) ===
    # Shallow copy of PendingOrder.trades at fill time. Each BrokerTrade is one
    # atomic execution. Single-fill MARKET → list length 1. Multi-fill LIMIT
    # (live, after #342) or order-book sim (#143) → list length N.
    entry_trades: List[BrokerTrade] = field(default_factory=list)

    # === Submission Slippage Audit (#340) ===
    # Carried over from PendingOrder.submission at fill time (#345).
    # Trade-channel mid price at the entry submission moment. Used by the
    # SLIPPAGE audit channel (live) and by post-run analysis (sim) to compute
    # entry slippage = entry_price - entry_submission.tick_mid_price.
    entry_submission: SubmissionMetadata = field(default_factory=SubmissionMetadata)

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

    # === Excursion (MAE/MFE) — running extrema over the position's life (#389) ===
    # Tracked per tick in update_current_price; copied to TradeRecord at close.
    mae_pnl: float = 0.0    # worst (most negative) gross unrealized P&L
    mfe_pnl: float = 0.0    # best (most positive) gross unrealized P&L
    mae_price: float = 0.0  # current_price at the worst excursion (seeded to entry)
    mfe_price: float = 0.0  # current_price at the best excursion (seeded to entry)

    def __post_init__(self):
        # Seed excursion prices at entry → "no move" reports as 0 distance (#389).
        self.mae_price = self.entry_price
        self.mfe_price = self.entry_price

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

        # Calculate P&L: points * tick_value * lots - all fees
        gross_pnl = gross_pnl_from_price_diff(price_diff, digits, tick_value, self.lots)
        total_fees = self.get_total_fees()

        # Store gross_pnl for trade record
        self.gross_pnl = gross_pnl
        self.unrealized_pnl = gross_pnl - total_fees

        # Track max adverse / favorable excursion over the position's life (#389).
        # gross_pnl (pre-fee) is the excursion axis → fee-noise-free; record the
        # price at each extreme for the SL-calibration read.
        if gross_pnl < self.mae_pnl:
            self.mae_pnl = gross_pnl
            self.mae_price = self.current_price
        if gross_pnl > self.mfe_pnl:
            self.mfe_pnl = gross_pnl
            self.mfe_price = self.current_price

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
        """Check if position is still open (includes partially closed)"""
        return self.status in (PositionStatus.OPEN, PositionStatus.PARTIALLY_CLOSED)
