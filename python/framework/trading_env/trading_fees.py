"""
FiniexTestingIDE - Trading Fee System
Polymorphic fee objects for different broker cost models

Architecture:
- AbstractTradingFee - Base class for all fees
- SpreadFee - Bid/Ask spread cost (fully implemented)
- SwapFee - Overnight interest (prepared, calculation deferred)
- CommissionFee - ECN commission (prepared, calculation deferred)
- MakerTakerFee - Crypto exchange fees (prepared, calculation deferred)

Each Position contains List[AbstractTradingFee] that accumulate over time.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from enum import Enum


class FeeType(Enum):
    """Trading fee type classification"""
    SPREAD = "spread"
    SWAP = "swap"
    COMMISSION = "commission"
    MAKER_TAKER = "maker_taker"


class FeeStatus(Enum):
    """Fee payment status"""
    PENDING = "pending"      # Fee calculated but not yet applied
    APPLIED = "applied"      # Fee deducted from balance
    DEFERRED = "deferred"    # Fee will be applied later (e.g., swap on close)


@dataclass
class AbstractTradingFee(ABC):
    """
    Abstract base class for all trading fees.

    All fee types inherit from this and implement calculate_cost().
    Fees are attached to positions and accumulated over the position lifecycle.

    Polymorphic design allows different brokers to use different fee models
    without changing the Position or Portfolio code.
    """
    fee_type: FeeType
    status: FeeStatus
    timestamp: datetime

    # Cost in account currency
    cost: float = 0.0

    # Optional metadata for fee-specific details
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    @abstractmethod
    def calculate_cost(self, **kwargs) -> float:
        """
        Calculate fee cost in account currency.

        Implementation varies by fee type.
        Returns absolute cost (always positive, even if swap is credit).
        """
        pass

    def apply(self):
        """Mark fee as applied to balance"""
        self.status = FeeStatus.APPLIED

    def get_display_name(self) -> str:
        """Get human-readable fee name"""
        return self.fee_type.value.replace('_', ' ').title()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(cost={self.cost:.2f}, status={self.status.value})"


# ============================================
# Concrete Fee Types
# ============================================

@dataclass
class SpreadFee(AbstractTradingFee):
    """
    Spread Fee - Implicit cost from bid/ask difference.

    FULLY IMPLEMENTED - Calculated from LIVE tick data at order entry.

    Calculation:
        spread_points = (ask - bid) * (10 ** digits)
        cost = spread_points * tick_value * lots

    Example (EURUSD):
        bid = 1.17408, ask = 1.17433
        spread = 0.00025 (2.5 pips)
        digits = 5
        spread_points = 0.00025 * 100000 = 25 points
        cost = 25 * 0.85155 * 0.1 = 2.13 EUR
    """

    # Required for calculation
    bid: float = 0.0
    ask: float = 0.0
    lots: float = 0.0
    tick_value: float = 0.0
    digits: int = 5

    def __init__(
        self,
        bid: float,
        ask: float,
        lots: float,
        tick_value: float,
        digits: int = 5,
        timestamp: Optional[datetime] = None
    ):
        """
        Initialize spread fee from live tick data.

        Args:
            bid: Current bid price
            ask: Current ask price
            lots: Order size
            tick_value: Value per tick per lot
            digits: Symbol decimal places
            timestamp: Fee timestamp (defaults to now)
        """
        super().__init__(
            fee_type=FeeType.SPREAD,
            status=FeeStatus.APPLIED,  # Spread is immediate
            timestamp=timestamp or datetime.now()
        )

        self.bid = bid
        self.ask = ask
        self.lots = lots
        self.tick_value = tick_value
        self.digits = digits

        # Calculate cost immediately
        self.cost = self.calculate_cost()

        # Store metadata
        self.metadata = {
            'bid': bid,
            'ask': ask,
            'spread_raw': ask - bid,
            'spread_points': (ask - bid) * (10 ** digits),
            'lots': lots
        }

    def calculate_cost(self, **kwargs) -> float:
        """
        Calculate spread cost.

        Formula:
            spread_points = (ask - bid) * (10^digits)
            cost = spread_points * tick_value * lots

        Returns:
            Spread cost in account currency
        """
        spread_raw = self.ask - self.bid
        spread_points = spread_raw * (10 ** self.digits)
        cost = spread_points * self.tick_value * self.lots

        return abs(cost)  # Always positive


@dataclass
class SwapFee(AbstractTradingFee):
    """
    Swap Fee - Overnight interest for holding positions.

    PREPARED - Calculation deferred to Post-MVP.

    Accumulates daily at broker rollover time (usually 00:00 server time).
    Can be positive (credit) or negative (debit) depending on interest differential.

    Future Implementation:
        - Check if position held overnight
        - Apply swap_long or swap_short from broker config
        - Accumulate daily until position closed
        - Triple swap on Wednesdays (rollover for weekend)

    Example (EURUSD from JSON):
        swap_long: -8.75 points (you pay)
        swap_short: 4.34 points (you receive)

        Long position held 3 nights:
        cost = -8.75 * 3 * tick_value * lots = -22.31 EUR (debit)
    """

    # Swap configuration
    # From broker config (swap_long or swap_short)
    swap_rate_points: float = 0.0
    days_held: int = 0
    tick_value: float = 0.0
    lots: float = 0.0

    def __init__(
        self,
        swap_rate_points: float,
        days_held: int,
        tick_value: float,
        lots: float,
        timestamp: Optional[datetime] = None
    ):
        """
        Initialize swap fee (calculation deferred).

        Args:
            swap_rate_points: Swap rate from broker config
            days_held: Number of overnight holds
            tick_value: Value per tick per lot
            lots: Position size
            timestamp: Fee timestamp
        """
        super().__init__(
            fee_type=FeeType.SWAP,
            status=FeeStatus.DEFERRED,  # Applied at position close
            timestamp=timestamp or datetime.now()
        )

        self.swap_rate_points = swap_rate_points
        self.days_held = days_held
        self.tick_value = tick_value
        self.lots = lots

        # Defer calculation until needed
        self.cost = 0.0

        self.metadata = {
            'swap_rate_points': swap_rate_points,
            'days_held': days_held,
            'implementation_status': 'deferred_post_mvp'
        }

    def calculate_cost(self, **kwargs) -> float:
        """
        Calculate swap cost (deferred).

        Post-MVP Implementation:
            cost = swap_rate_points * days_held * tick_value * lots

        Returns:
            0.0 (deferred to Post-MVP)
        """
        # TODO: Implement swap calculation Post-MVP
        # cost = self.swap_rate_points * self.days_held * self.tick_value * self.lots
        return 0.0


@dataclass
class CommissionFee(AbstractTradingFee):
    """
    Commission Fee - Explicit broker commission.

    PREPARED - Calculation deferred to Post-MVP.

    Common in ECN brokers (e.g., IC Markets charges $7 per lot).
    Can be per-lot or percentage-based.

    Types:
    - Per Lot: Fixed amount per lot (e.g., $7/lot)
    - Percentage: Percentage of order value (e.g., 0.1%)
    - Per Side: Charged on entry, exit, or both

    Future Implementation:
        - Read commission config from broker JSON
        - Apply on order entry and/or close
        - Track total commission separately

    Example:
        IC Markets ECN: $7 per lot
        Order: 0.5 lots EURUSD
        Commission = 7 * 0.5 = $3.50 (entry) + $3.50 (exit) = $7 total
    """

    # Commission configuration
    commission_mode: str = "per_lot"  # "per_lot" or "percentage"
    commission_rate: float = 0.0  # $7 per lot OR 0.1% etc.
    lots: float = 0.0
    order_value: float = 0.0  # For percentage calculation
    side: str = "entry"  # "entry", "exit", or "both"

    def __init__(
        self,
        commission_mode: str,
        commission_rate: float,
        lots: float,
        order_value: float = 0.0,
        side: str = "entry",
        timestamp: Optional[datetime] = None
    ):
        """
        Initialize commission fee (calculation deferred).

        Args:
            commission_mode: "per_lot" or "percentage"
            commission_rate: Commission amount or percentage
            lots: Order size
            order_value: Order value in account currency (for percentage)
            side: "entry", "exit", or "both"
            timestamp: Fee timestamp
        """
        super().__init__(
            fee_type=FeeType.COMMISSION,
            status=FeeStatus.PENDING,
            timestamp=timestamp or datetime.now()
        )

        self.commission_mode = commission_mode
        self.commission_rate = commission_rate
        self.lots = lots
        self.order_value = order_value
        self.side = side

        # Defer calculation
        self.cost = 0.0

        self.metadata = {
            'mode': commission_mode,
            'rate': commission_rate,
            'side': side,
            'implementation_status': 'deferred_post_mvp'
        }

    def calculate_cost(self, **kwargs) -> float:
        """
        Calculate commission (deferred).

        Post-MVP Implementation:
            if mode == "per_lot":
                cost = commission_rate * lots
            elif mode == "percentage":
                cost = order_value * (commission_rate / 100)

        Returns:
            0.0 (deferred to Post-MVP)
        """
        # TODO: Implement commission calculation Post-MVP
        return 0.0


@dataclass
class MakerTakerFee(AbstractTradingFee):
    """
    Maker/Taker Fee - Crypto exchange fee model.

    PREPARED - Calculation deferred to Post-MVP.

    Maker: Adds liquidity (limit orders) - lower fee
    Taker: Removes liquidity (market orders) - higher fee

    Common in crypto exchanges (Kraken, Binance, Coinbase).
    Fees are percentage-based on order value.

    Future Implementation:
        - Detect if order is maker or taker
        - Read maker_fee / taker_fee from broker config
        - Calculate as percentage of order value

    Example (Kraken from dummy config):
        maker_fee: 0.16%
        taker_fee: 0.26%

        Market buy 1 BTC @ $60,000:
        cost = 60000 * 0.0026 = $156
    """

    # Fee configuration
    is_maker: bool = False  # False = taker
    maker_rate: float = 0.0  # Percentage (e.g., 0.16)
    taker_rate: float = 0.0  # Percentage (e.g., 0.26)
    order_value: float = 0.0

    def __init__(
        self,
        is_maker: bool,
        maker_rate: float,
        taker_rate: float,
        order_value: float,
        timestamp: Optional[datetime] = None
    ):
        """
        Initialize maker/taker fee (calculation deferred).

        Args:
            is_maker: True if maker order, False if taker
            maker_rate: Maker fee percentage
            taker_rate: Taker fee percentage
            order_value: Order value in account currency
            timestamp: Fee timestamp
        """
        super().__init__(
            fee_type=FeeType.MAKER_TAKER,
            status=FeeStatus.PENDING,
            timestamp=timestamp or datetime.now()
        )

        self.is_maker = is_maker
        self.maker_rate = maker_rate
        self.taker_rate = taker_rate
        self.order_value = order_value

        # Defer calculation
        self.cost = 0.0

        self.metadata = {
            'is_maker': is_maker,
            'maker_rate': maker_rate,
            'taker_rate': taker_rate,
            'implementation_status': 'deferred_post_mvp'
        }

    def calculate_cost(self, **kwargs) -> float:
        """
        Calculate maker/taker fee (deferred).

        Post-MVP Implementation:
            rate = maker_rate if is_maker else taker_rate
            cost = order_value * (rate / 100)

        Returns:
            0.0 (deferred to Post-MVP)
        """
        # TODO: Implement maker/taker fee calculation Post-MVP
        return 0.0


# ============================================
# Helper Functions
# ============================================

def create_spread_fee_from_tick(
    bid: float,
    ask: float,
    lots: float,
    tick_value: float,
    digits: int = 5
) -> SpreadFee:
    """
    Factory function to create SpreadFee from live tick data.

    Use this in TradeSimulator when executing market orders.

    Args:
        bid: Current bid price from tick
        ask: Current ask price from tick
        lots: Order size
        tick_value: From broker symbol config
        digits: Symbol decimal places

    Returns:
        SpreadFee instance with calculated cost
    """
    return SpreadFee(
        bid=bid,
        ask=ask,
        lots=lots,
        tick_value=tick_value,
        digits=digits
    )
