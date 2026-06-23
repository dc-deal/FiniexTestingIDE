"""
FiniexTestingIDE - Trading Fee System
Polymorphic fee objects for different broker cost models

Architecture:
- SpreadFee - Bid/Ask spread cost (fully implemented)
- SwapFee - Overnight interest (prepared, calculation deferred)
- CommissionFee - ECN commission (prepared, calculation deferred)
- MakerTakerFee - Crypto exchange fees (fully implemented)

Each Position contains List[AbstractTradingFee] that accumulate over time.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from enum import Enum

from python.framework.trading_env.abstract_trading_fee import AbstractTradingFee
from python.framework.types.trading_env_types.broker_types import FeeStatus, FeeType
from python.framework.types.market_types.market_data_types import TickData

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
            timestamp=timestamp or datetime.now(timezone.utc)
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
    Swap Fee - Overnight financing for holding a position across a broker rollover.

    One SwapFee represents ONE accrued rollover crossing (charged at the rollover
    instant, #365). Signed: a debit (you pay) is a positive cost, a credit (you
    receive) is a negative cost — overriding the always-positive base contract,
    since swap can be a credit (positive carry). The sign matches the portfolio's
    net P&L = gross - total_fees: a positive cost reduces P&L, a negative cost
    (credit) raises it.

    Calculation (points mode):
        cost = -(swap_rate_points * days_held * tick_value * lots)

    where days_held is the swap-day multiplier of this crossing (1 normal night, 3
    on the triple-swap weekday). swap_rate_points is the broker's swap_long (long
    position) or swap_short (short). tick_value is the position's entry_tick_value
    (a deterministic, eval-timing-independent anchor).

    Example (EURUSD, USD account, 1.0 lot, tick_value 1.0):
        swap_long  = -7.85 (you pay)     -> cost = -(-7.85 * 1 * 1.0 * 1.0) = +7.85 (debit)
        swap_short = +3.80 (you receive) -> cost = -( +3.80 * 1 * 1.0 * 1.0) = -3.80 (credit)
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
        Initialize swap fee for one accrued rollover crossing.

        Args:
            swap_rate_points: Broker swap rate in points (swap_long or swap_short)
            days_held: Swap-day multiplier for this crossing (1, or 3 on triple day)
            tick_value: Value per point per lot (entry_tick_value anchor)
            lots: Position size
            timestamp: Rollover instant the swap is booked at
        """
        super().__init__(
            fee_type=FeeType.SWAP,
            status=FeeStatus.APPLIED,  # Charged at the rollover instant
            timestamp=timestamp or datetime.now(timezone.utc)
        )

        self.swap_rate_points = swap_rate_points
        self.days_held = days_held
        self.tick_value = tick_value
        self.lots = lots

        # Calculate cost immediately (signed)
        self.cost = self.calculate_cost()

        self.metadata = {
            'swap_rate_points': swap_rate_points,
            'days_held': days_held,
            'is_triple': days_held >= 3
        }

    def calculate_cost(self, **kwargs) -> float:
        """
        Calculate signed swap cost for this rollover crossing.

        Formula (points mode):
            cost = -(swap_rate_points * days_held * tick_value * lots)

        Returns:
            Signed cost in account currency — positive = debit (reduces P&L),
            negative = credit (raises P&L). See class docstring for the convention.
        """
        return -(self.swap_rate_points * self.days_held * self.tick_value * self.lots)


@dataclass
class CommissionFee(AbstractTradingFee):
    """
    Commission Fee - Explicit broker commission.

    PREPARED - Calculation deferred to Post-V1.

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
            timestamp=timestamp or datetime.now(timezone.utc)
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
            'implementation_status': 'deferred_post_v1'
        }

    def calculate_cost(self, **kwargs) -> float:
        """
        Calculate commission (deferred).

        Post-V1 Implementation:
            if mode == "per_lot":
                cost = commission_rate * lots
            elif mode == "percentage":
                cost = order_value * (commission_rate / 100)

        Returns:
            0.0 (deferred to Post-V1)
        """
        # TODO: Implement commission calculation Post-V1
        return 0.0


@dataclass
class MakerTakerFee(AbstractTradingFee):
    """
    Maker/Taker Fee - Crypto exchange fee model.

    FULLY IMPLEMENTED - Percentage-based fee on order value.

    Maker: Adds liquidity (limit orders) - lower fee
    Taker: Removes liquidity (market orders) - higher fee

    Common in crypto exchanges (Kraken, Binance, Coinbase).
    Fees are percentage-based on order value.

    Calculation:
        rate = maker_rate if is_maker else taker_rate
        cost = order_value * (rate / 100)

    Example (Kraken):
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
            Initialize maker/taker fee.

            Args:
                is_maker: True if maker order, False if taker
                maker_rate: Maker fee percentage
                taker_rate: Taker fee percentage
                order_value: Order value in account currency
                timestamp: Fee timestamp
            """
        super().__init__(
            fee_type=FeeType.MAKER_TAKER,
            status=FeeStatus.APPLIED,  # Fee applied immediately on fill
            timestamp=timestamp or datetime.now(timezone.utc)
        )

        self.is_maker = is_maker
        self.maker_rate = maker_rate
        self.taker_rate = taker_rate
        self.order_value = order_value

        # Calculate cost immediately
        self.cost = self.calculate_cost()

        # Store metadata
        self.metadata = {
            'is_maker': is_maker,
            'maker_rate': maker_rate,
            'taker_rate': taker_rate,
            'order_value': order_value,
            'applied_rate': maker_rate if is_maker else taker_rate
        }

    def calculate_cost(self, **kwargs) -> float:
        """
        Calculate maker/taker fee.

        Formula:
            rate = maker_rate if is_maker else taker_rate
            cost = order_value * (rate / 100)

        Returns:
            Fee cost in account currency
        """
        rate = self.maker_rate if self.is_maker else self.taker_rate
        cost = self.order_value * (rate / 100)
        return abs(cost)
