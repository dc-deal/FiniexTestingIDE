from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from python.framework.trading_env.abstract_trading_fee import AbstractTradingFee
from python.framework.types.portfolio_types.portfolio_trade_record_types import EntryType
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.broker_types import FeeType
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    ProtectiveLevelEnforcement,
)
from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata
from python.framework.utils.trading_math.pnl_math import gross_pnl_from_price_diff
from python.framework.utils.trading_math.price_trigger import mid_price


class PositionStatus(Enum):
    """Position status"""
    OPEN = 'open'
    CLOSED = 'closed'
    PARTIALLY_CLOSED = 'partially_closed'


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
    comment: str = ''
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
    # The book at the CLOSING fill, the mirror of entry_bid/entry_ask above. Stamped so the
    # crossed spread stays measurable after the fact: the effective half-spread is the
    # distance from each fill to the MIDPOINT at that moment, which is how the literature
    # defines it (Johnson, *Algorithmic Trading & DMA*, ch. 6) and the only benchmark neutral
    # between the two sides. NOT the submission mid — that one belongs to the slippage audit
    # (#340) and answers how far the market moved while the order was in flight.
    exit_bid: float = 0.0
    exit_ask: float = 0.0
    exit_tick_value: float = 0.0
    digits: int = 5
    contract_size: int = 100000
    gross_pnl: float = 0.0

    # === Pip Unit (#167) — authoritative pip size + report unit label ===
    # Stamped from the adapter at open; copied to TradeRecord at close so every
    # report surface (console / CSV / API) shows MAE/MFE in the correct unit
    # (pip on Forex, tick on crypto) without re-deriving downstream.
    pip_size: float = 0.0
    price_unit: str = ''

    # === Tick Index (for backtesting analysis) ===
    entry_tick_index: int = 0
    exit_tick_index: int = 0

    # === Excursion (MAE/MFE) — running extrema over the position's life (#389) ===
    # Tracked per tick in update_current_price; copied to TradeRecord at close.
    mae_pnl: float = 0.0    # worst (most negative) gross unrealized P&L
    mfe_pnl: float = 0.0    # best (most positive) gross unrealized P&L
    mae_price: float = 0.0  # current_price at the worst excursion (seeded to entry)
    mfe_price: float = 0.0  # current_price at the best excursion (seeded to entry)

    # === Venue-held protection (#503) — the order the venue holds for this position ===
    # Set from the CONFIRMATION of the protective order, never from the declaration:
    # between submitting one and hearing back, nobody at the venue holds anything, so the
    # local check has to stay awake. Both fields travel into the cold-start carry-over —
    # the projection sees the position, not the PendingOrder, and the broker reference is
    # the only key that can still ask "did it fill?" after a restart.
    protective_order_id: Optional[str] = None
    protective_broker_ref: Optional[str] = None
    # The WIRE key that order was sent under (#487). The reference above is what asks the
    # venue "did it fill?"; this is what asks "is it still there?" on the route that needs
    # no reference — and after a restart it cannot be recomputed, because it was minted by
    # a session whose discriminator is gone. Re-deriving one from THIS session would stamp
    # our name on a predecessor's order.
    protective_client_order_id: Optional[str] = None

    # === Swap accrual (#365) — last rollover instant already charged ===
    # Seeded to entry_time in __post_init__; advanced as overnight swap accrues.
    swap_accrued_until: Optional[datetime] = None

    def __post_init__(self):
        # Seed excursion prices at entry → "no move" reports as 0 distance (#389). Only
        # where the field is still unset: a RESTORED position (#355) carries extrema from a
        # life that already happened, and a running maximum cannot be recomputed after the
        # fact — seeding unconditionally discarded them without a word.
        if self.mae_price == 0.0:
            self.mae_price = self.entry_price
        if self.mfe_price == 0.0:
            self.mfe_price = self.entry_price

        # Swap accrual starts at the position's open instant (#365).
        if self.swap_accrued_until is None:
            self.swap_accrued_until = self.entry_time

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

    def _spread_leg(self, fill_price: float, bid: float, ask: float,
                    tick_value: float, lots: float, is_entry: bool) -> float:
        """
        The effective half-spread of ONE leg, in account currency.

        Signed by trade direction, which is the standard TCA construction
        (`2 · q · (P − M)`, q = +1 buy / −1 sell): a fill on the far side of the mid is a
        cost, a fill INSIDE the spread is a price improvement and comes out negative. Taking
        an absolute value here would report a resting limit's improvement as a cost.

        Args:
            fill_price: The price this leg filled at
            bid: Bid at that fill
            ask: Ask at that fill
            tick_value: Tick value at that fill
            lots: Size to charge the distance over
            is_entry: True for the opening leg, False for the closing one

        Returns:
            The leg's effective spread cost, 0.0 when no book was recorded
        """
        if bid <= 0.0 or ask <= 0.0:
            return 0.0

        mid = mid_price(bid, ask)
        buying = (self.direction == OrderDirection.LONG) == is_entry
        distance = (fill_price - mid) if buying else (mid - fill_price)
        return gross_pnl_from_price_diff(distance, self.digits, tick_value, lots)

    def get_spread_cost(self) -> float:
        """
        The spread this position crossed — MEASURED, never charged.

        It used to sum `SpreadFee` objects, and that was the double charge (#244): a market
        entry takes the ask and the close takes the bid, so one full spread width is already
        inside `gross_pnl` before any fee. Booking it again as a fee subtracted the same
        quantity twice. The cost is real and is still paid; what it is not, is a fee — so it
        is derived here and stays out of `get_total_fees()`.

        On a static book a market round trip comes to exactly one full spread, which is the
        textbook result: a buy at the ask and an immediate sell at the bid costs the spread,
        half of it each way.

        Sized on `original_lots`, because the caller that splits a partial close multiplies
        by the closed fraction; the shrinking `lots` would apply that ratio twice.

        Returns:
            The effective spread cost of both legs, entry only while still open
        """
        cost = self._spread_leg(
            self.entry_price, self.entry_bid, self.entry_ask,
            self.entry_tick_value, self.original_lots, is_entry=True)

        if self.close_price is not None:
            cost += self._spread_leg(
                self.close_price, self.exit_bid, self.exit_ask,
                self.exit_tick_value, self.original_lots, is_entry=False)
        return cost

    def spread_cost_for_partial(self, close_ratio: float, exit_price: float,
                                exit_bid: float, exit_ask: float,
                                exit_tick_value: float) -> float:
        """
        The spread attributable to ONE partial close.

        The position is still open here, so its own exit book is not set and cannot be —
        the closing book belongs to this partial, not to the position. Hence the explicit
        parameters: the entry leg is pro-rated by the closed fraction, the exit leg is
        measured against the book this partial actually filled against.

        Args:
            close_ratio: Closed lots ÷ current lots
            exit_price: Price this partial closed at
            exit_bid: Bid at that fill
            exit_ask: Ask at that fill
            exit_tick_value: Tick value at that fill

        Returns:
            The partial's effective spread cost
        """
        entry_leg = self._spread_leg(
            self.entry_price, self.entry_bid, self.entry_ask,
            self.entry_tick_value, self.original_lots, is_entry=True) * close_ratio

        exit_leg = self._spread_leg(
            exit_price, exit_bid, exit_ask,
            exit_tick_value, self.lots * close_ratio, is_entry=False)

        return entry_leg + exit_leg

    def get_commission_cost(self) -> float:
        """
        Get the total charged per-side cost — commission and maker/taker alike.

        MAKER_TAKER belongs here and used to be missing, which is why every Kraken trade
        record reported `commission_cost` 0.0 beside a real `total_fees`: the fee existed,
        and no per-trade column received it. A per-side charge IS a commission in the
        standard taxonomy (explicit costs: commission, fees, taxes), so this is where it
        goes rather than into a fourth column.

        The maker/taker SPLIT is not lost by folding: `ExecutionRow` carries `fee` and
        `liquidity` per fill in both pipelines — finer than per trade, since one trade can
        hold fills of both kinds — and `CostBreakdown` keeps its `maker_fee` / `taker_fee`
        totals for the run.

        Returns:
            The sum of every charged per-side fee on this position
        """
        charged = (self.get_fees_by_type(FeeType.COMMISSION)
                   + self.get_fees_by_type(FeeType.MAKER_TAKER))
        return sum(fee.cost for fee in charged)

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

    def protective_enforcement(
        self,
        run_default: ProtectiveLevelEnforcement
    ) -> ProtectiveLevelEnforcement:
        """
        Who holds THIS position's protective level (#503).

        Derived from the broker reference rather than stored beside it: a fourth carrier
        of the same truth is how the console came to print a stop nobody held (#500). A
        run is mixed from now on — one position's level can rest at the venue while the
        next one's does not — so the run-wide answer is only the fallback.

        MARGIN ANCHOR (2026-09-10, #209): this derivation assumes the venue holds the
        level as a STANDALONE ORDER, which is Kraken spot's shape. MT5 holds it ON the
        position — there is no separate order and therefore no broker reference to derive
        from, so a margin venue that genuinely enforces a level would still read LOCAL
        here. Not a defect today (MT5 declares venue_held_protective_orders=False, so the
        case cannot arise), and deliberately not modelled ahead of the account model that
        needs it. #209 decides whether the answer becomes a three-way one.

        Args:
            run_default: What the executor answers for the run as a whole

        Returns:
            VENUE once the venue has confirmed an order for this position, else the default
        """
        if self.protective_broker_ref:
            return ProtectiveLevelEnforcement.VENUE
        return run_default

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
