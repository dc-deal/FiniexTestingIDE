"""
FiniexTestingIDE - Algo Position
The open position as an algo sees it (#653).

The engine marks its positions lazily: at spot a position is marked only when its price passes an
extreme, so its stored price and P&L lag the market, and a position a fill just opened carries none
until something marks it. An algo therefore never reads those stored values: what moves with the
market — the close price, the P&L, the excursion, the fees — is computed from the latest quote when
the algo reads it, through the same formula the engine marks with. Everything else is the value the
engine stored. A mask an algo keeps across passes stays current, because it computes on every read.
"""

from datetime import datetime
from typing import Callable, Optional

from python.framework.trading_env.algo_masks.abstract_algo_mask import AbstractAlgoMask
from python.framework.types.portfolio_types.portfolio_trade_record_types import EntryType
from python.framework.types.portfolio_types.portfolio_types import (
    Position,
    PositionStatus,
    PositionValuation,
)
from python.framework.types.trading_env_types.order_types import OrderDirection


class AlgoPosition(AbstractAlgoMask):
    """
    A read-only view of one position, its market-dependent fields computed when read.

    The valuation it is handed is the portfolio's: it values the position at the latest quote of
    its symbol, books the position's swap up to the canonical clock first (margin only, as reading
    the account info does), and writes nothing else. A closed position shows what it stored.
    """

    __slots__ = ('_valuate',)

    def __init__(self, position: Position,
                 valuate: Callable[[Position], Optional[PositionValuation]]):
        """
        Wrap one engine position.

        Args:
            position: The engine's position
            valuate: The portfolio's valuation — None while its symbol has no quote yet
        """
        super().__init__(position)
        object.__setattr__(self, '_valuate', valuate)

    # ============================================
    # Stored — what the engine recorded
    # ============================================

    @property
    def position_id(self) -> str:
        """The position's identifier — the one close_position and modify_position take."""
        return self._source.position_id

    @property
    def symbol(self) -> str:
        """The traded symbol."""
        return self._source.symbol

    @property
    def direction(self) -> OrderDirection:
        """LONG or SHORT."""
        return self._source.direction

    @property
    def status(self) -> PositionStatus:
        """Open, partially closed, or closed."""
        return self._source.status

    @property
    def lots(self) -> float:
        """The lots still open."""
        return self._source.lots

    @property
    def original_lots(self) -> float:
        """The lots the position opened with."""
        return self._source.original_lots

    @property
    def entry_price(self) -> float:
        """The price the position opened at."""
        return self._source.entry_price

    @property
    def entry_time(self) -> datetime:
        """When the position opened, on the canonical clock."""
        return self._source.entry_time

    @property
    def entry_type(self) -> EntryType:
        """How the position was entered — a market order, a limit or a stop."""
        return self._source.entry_type

    @property
    def stop_loss(self) -> Optional[float]:
        """The stop-loss level, None when none is set."""
        return self._source.stop_loss

    @property
    def take_profit(self) -> Optional[float]:
        """The take-profit level, None when none is set."""
        return self._source.take_profit

    @property
    def comment(self) -> str:
        """The comment the opening order carried."""
        return self._source.comment

    @property
    def pip_size(self) -> float:
        """The symbol's pip size."""
        return self._source.pip_size

    @property
    def digits(self) -> int:
        """The symbol's price digits."""
        return self._source.digits

    @property
    def contract_size(self) -> int:
        """The symbol's contract size."""
        return self._source.contract_size

    @property
    def price_unit(self) -> str:
        """The unit the symbol's price is quoted in."""
        return self._source.price_unit

    @property
    def entry_bid(self) -> float:
        """The bid at entry."""
        return self._source.entry_bid

    @property
    def entry_ask(self) -> float:
        """The ask at entry."""
        return self._source.entry_ask

    @property
    def entry_tick_value(self) -> float:
        """The value of one point in the account currency at entry."""
        return self._source.entry_tick_value

    # ============================================
    # Computed when read — what moves with the market
    # ============================================

    @property
    def current_price(self) -> Optional[float]:
        """The price it would close at now (bid for a LONG, ask for a SHORT); None before a quote."""
        if self._is_closed():
            return self._source.current_price
        valuation = self._valuation()
        return valuation.current_price if valuation else None

    @property
    def gross_pnl(self) -> Optional[float]:
        """The P&L before fees, at the latest quote; None before its symbol's first quote."""
        if self._is_closed():
            return self._source.gross_pnl
        valuation = self._valuation()
        return valuation.gross_pnl if valuation else None

    @property
    def unrealized_pnl(self) -> Optional[float]:
        """The P&L after every fee so far, at the latest quote; None before its symbol's first quote."""
        if self._is_closed():
            return self._source.unrealized_pnl
        valuation = self._valuation()
        return valuation.unrealized_pnl if valuation else None

    @property
    def total_fees(self) -> float:
        """Every fee the position carries so far, its swap booked up to the clock."""
        self._valuation()
        return self._source.get_total_fees()

    @property
    def mae_pnl(self) -> float:
        """The worst gross P&L of the position's life, the latest quote included."""
        valuation = self._valuation()
        if valuation is None:
            return self._source.mae_pnl
        return min(self._source.mae_pnl, valuation.gross_pnl)

    @property
    def mae_price(self) -> float:
        """The close price at the worst gross P&L, the latest quote included."""
        valuation = self._valuation()
        if valuation is None or valuation.gross_pnl >= self._source.mae_pnl:
            return self._source.mae_price
        return valuation.current_price

    @property
    def mfe_pnl(self) -> float:
        """The best gross P&L of the position's life, the latest quote included."""
        valuation = self._valuation()
        if valuation is None:
            return self._source.mfe_pnl
        return max(self._source.mfe_pnl, valuation.gross_pnl)

    @property
    def mfe_price(self) -> float:
        """The close price at the best gross P&L, the latest quote included."""
        valuation = self._valuation()
        if valuation is None or valuation.gross_pnl <= self._source.mfe_pnl:
            return self._source.mfe_price
        return valuation.current_price

    def _is_closed(self) -> bool:
        """
        Whether the position has closed — its values are then what it closed with.

        Returns:
            True for a closed position
        """
        return self._source.status is PositionStatus.CLOSED

    def _valuation(self) -> Optional[PositionValuation]:
        """
        The position at the latest quote, or None — closed, no quote for its symbol yet, or no
        longer in the book.

        A price the market has not given yet is unknown, not zero: the price and P&L fields then
        read None, while the excursion fields keep their recorded history.

        Returns:
            The valuation, or None
        """
        if self._is_closed():
            return None
        return self._valuate(self._source)
