"""
FiniexTestingIDE - Portfolio Manager ()
Tracks account balance, equity, open positions, and P&L with full fee tracking

 CHANGES:
- Direct attributes for statistics (_total_trades, _winning_trades, etc.)
- CostBreakdown object instead of dict for _cost_tracking
- Always-copy public API (using replace())
- Cleaner, more maintainable code structure
- CURRENCY: Changed 'currency' to 'account_currency' for clarity
"""

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trading_fee import AbstractTradingFee
from python.framework.types.broker_types import FeeType, SymbolSpecification
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_trade_record_types import CloseType, CloseReason, EntryType, TradeRecord
from python.framework.types.portfolio_types import Position, PositionStatus

from ..types.order_types import ModificationRejectionReason, ModificationResult, OrderDirection
from python.framework.types.trading_env_stats_types import AccountInfo, CostBreakdown
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.market_data_types import TickData


class _UnsetType:
    """Sentinel to distinguish 'not provided' from 'set to None' (remove SL/TP)."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetType()


class PortfolioManager:
    """
    Portfolio Manager - Tracks account state and positions.

    :
    - Direct attributes for statistics (no nested _statistics object)
    - CostBreakdown object for _cost_tracking (separate concern)
    - Always-copy public API for safety
    - FULLY TYPED: Returns dataclasses instead of dicts
    - CURRENCY: Uses 'account_currency' instead of 'currency' for clarity
    """

    def __init__(
        self,
        logger: AbstractLogger,
        initial_balance: float,
        account_currency: str,
        broker_config: BrokerConfig,
        leverage: int,
        margin_call_level: float,
        stop_out_level: float,
        trade_history_max: int = 5000,
    ):
        """
        Initialize portfolio manager.

        Args:
            initial_balance: Starting balance
            account_currency: Account currency (e.g., "USD", "EUR", "JPY")
            broker_config: Broker configuration (for symbol specifications)
            leverage: Account leverage
            margin_call_level: Margin call threshold percentage
            stop_out_level: Stop out threshold percentage
            trade_history_max: Max trade records to retain (0=unlimited)
        """
        self._logger = logger
        self.initial_balance = initial_balance
        self.account_currency = account_currency
        self.broker_config = broker_config
        self.leverage = leverage
        self.margin_call_level = margin_call_level
        self.stop_out_level = stop_out_level
        self._last_conversion_rate: Optional[float] = None

        # Account state
        self.balance = initial_balance
        self.realized_pnl = 0.0

        # Positions
        self._positions_dirty = False  # Performance: Lazy evaluation state
        self.open_positions: Dict[str, Position] = {}
        self._trade_history_max = trade_history_max
        self._trade_history: deque[TradeRecord] = deque(
            maxlen=trade_history_max if trade_history_max > 0 else None
        )
        self._trade_history_limit_warned = False

        # Position counter
        self._position_counter = 0

        # Cost tracking as CostBreakdown object
        self._cost_tracking = CostBreakdown(currency=account_currency)

        # Statistics as direct attributes (no nested object)
        self._total_trades = 0
        self._total_long_trades = 0
        self._total_short_trades = 0
        self._winning_trades = 0
        self._losing_trades = 0
        self._total_profit = 0.0
        self._total_loss = 0.0
        self._max_drawdown = 0.0
        self._max_equity = initial_balance

        # Current market state (lazy evaluation)
        self._current_tick: Optional[TickData] = None
        self._current_prices: Dict[str, tuple[float, float]] = {}

    # ============================================
    # Position Management
    # ============================================

    # Generate position ID
    def get_next_position_id(self, symbol) -> str:
        self._position_counter += 1
        return f"pos_{symbol.lower()}_{self._position_counter}"

    def open_position(
        self,
        order_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        entry_price: float,
        entry_tick_value: float,
        entry_bid: float,
        entry_ask: float,
        digits: int,
        contract_size: int,
        entry_tick_index: int,
        entry_fee: Optional[AbstractTradingFee] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "",
        magic_number: int = 0,
        entry_type: EntryType = EntryType.MARKET
    ) -> Position:
        """
        Open new position.

        Accepts entry_fee (typically SpreadFee).
        """
        # order_id becomes position id.
        position_id = order_id

        # Create position
        position = Position(
            position_id=position_id,
            symbol=symbol,
            direction=direction,
            lots=lots,
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc),
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_type=entry_type,
            comment=comment,
            magic_number=magic_number,
            # Trade record fields
            entry_tick_value=entry_tick_value,
            entry_bid=entry_bid,
            entry_ask=entry_ask,
            digits=digits,
            contract_size=contract_size,
            entry_tick_index=entry_tick_index
        )

        # Attach entry fee
        if entry_fee:
            position.add_fee(entry_fee)

            # Update cost tracking object
            if entry_fee.fee_type == FeeType.SPREAD:
                self._cost_tracking.total_spread_cost += entry_fee.cost
            elif entry_fee.fee_type == FeeType.COMMISSION:
                self._cost_tracking.total_commission += entry_fee.cost

            self._cost_tracking.total_fees += entry_fee.cost

        # Add to open positions
        self.open_positions[position_id] = position

        return position

    def close_position_portfolio(
        self,
        position_id: str,
        exit_price: float,
        exit_tick_value: float,
        exit_tick_index: int,
        exit_fee: Optional[AbstractTradingFee] = None,
        close_reason: CloseReason = CloseReason.MANUAL
    ) -> float:
        """
        Close position and realize P&L.

        Args:
            position_id: Position to close
            exit_price: Price at which position is closed
            exit_tick_value: Tick value at exit for P&L conversion
            exit_tick_index: Tick counter at close time
            exit_fee: Optional exit fee (commission or final swap)
            close_reason: Why the position was closed

        Returns:
            Realized P&L amount
        """
        if position_id not in self.open_positions:
            raise ValueError(f"Position {position_id} not found")

        # Ensure position has latest P&L before closing
        self._ensure_positions_updated()

        position = self.open_positions[position_id]

        # Add exit fee if provided
        if exit_fee:
            position.add_fee(exit_fee)

            # Update cost tracking
            if exit_fee.fee_type == FeeType.COMMISSION:
                self._cost_tracking.total_commission += exit_fee.cost
            elif exit_fee.fee_type == FeeType.SWAP:
                self._cost_tracking.total_swap += exit_fee.cost

            self._cost_tracking.total_fees += exit_fee.cost

        # Calculate final P&L (unrealized_pnl already includes fees)
        realized_pnl = position.unrealized_pnl

        # Update balance
        self.balance += realized_pnl
        self.realized_pnl += realized_pnl

        # Mark position as closed
        position.status = PositionStatus.CLOSED
        position.close_time = datetime.now(timezone.utc)
        position.close_price = exit_price
        position.exit_tick_value = exit_tick_value
        position.exit_tick_index = exit_tick_index

        # Create TradeRecord from closed position
        trade_record = self._create_trade_record(position, CloseType.FULL, close_reason)
        if (self._trade_history_max > 0
                and not self._trade_history_limit_warned
                and len(self._trade_history) >= self._trade_history_max):
            self._trade_history_limit_warned = True
            self._logger.warning(
                f"⚠️ Trade history limit reached ({self._trade_history_max}). "
                f"Oldest entries will be discarded. Full history available in scenario log."
            )
        self._trade_history.append(trade_record)
        self._log_trade_record(trade_record)
        del self.open_positions[position_id]

        # Update statistics
        self._update_statistics(position, realized_pnl)

        return realized_pnl

    def _create_trade_record(
        self,
        position: Position,
        close_type: CloseType,
        close_reason: CloseReason = CloseReason.MANUAL
    ) -> TradeRecord:
        """
        Convert closed Position to TradeRecord.

        Args:
            position: Closed position with all data
            close_type: FULL or PARTIAL close
            close_reason: Why the position was closed

        Returns:
            TradeRecord with all fields for P&L verification
        """
        return TradeRecord(
            position_id=position.position_id,
            symbol=position.symbol,
            direction=position.direction,
            lots=position.lots,
            close_type=close_type,
            entry_price=position.entry_price,
            entry_time=position.entry_time,
            entry_tick_value=position.entry_tick_value,
            entry_bid=position.entry_bid,
            entry_ask=position.entry_ask,
            exit_price=position.close_price,
            exit_time=position.close_time,
            exit_tick_value=position.exit_tick_value,
            entry_tick_index=position.entry_tick_index,
            exit_tick_index=position.exit_tick_index,
            digits=position.digits,
            contract_size=position.contract_size,
            spread_cost=position.get_spread_cost(),
            commission_cost=position.get_commission_cost(),
            swap_cost=position.get_swap_cost(),
            total_fees=position.get_total_fees(),
            gross_pnl=position.gross_pnl,
            net_pnl=position.unrealized_pnl,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            close_reason=close_reason,
            entry_type=position.entry_type,
            comment=position.comment,
            magic_number=position.magic_number,
            account_currency=self.account_currency
        )

    # ============================================
    # Performance - Caching
    # ============================================

    def modify_position(
        self,
        position_id: str,
        new_stop_loss: Union[float, None, _UnsetType] = UNSET,
        new_take_profit: Union[float, None, _UnsetType] = UNSET
    ) -> ModificationResult:
        """
        Modify position SL/TP with validation.

        Uses UNSET sentinel to distinguish "don't change" from "set to None" (remove SL/TP).
        Validates levels against current prices before applying.

        Args:
            position_id: Position to modify
            new_stop_loss: New SL level (UNSET=no change, None=remove, float=set)
            new_take_profit: New TP level (UNSET=no change, None=remove, float=set)

        Returns:
            ModificationResult with success status and rejection reason
        """
        if position_id not in self.open_positions:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.POSITION_NOT_FOUND)

        position = self.open_positions[position_id]

        # Determine effective levels after modification (for cross-validation)
        effective_sl = position.stop_loss if isinstance(new_stop_loss, _UnsetType) else new_stop_loss
        effective_tp = position.take_profit if isinstance(new_take_profit, _UnsetType) else new_take_profit

        # Validate against current prices
        if position.symbol not in self._current_prices:
            self._logger.warning(
                f"No current price for {position.symbol} — cannot validate SL/TP")
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.NO_CURRENT_PRICE)

        bid, ask = self._current_prices[position.symbol]

        rejection = self._validate_sl_tp_levels(position.direction, bid, ask, effective_sl, effective_tp)
        if rejection is not None:
            return ModificationResult(success=False, rejection_reason=rejection)

        # Apply changes
        if not isinstance(new_stop_loss, _UnsetType):
            position.stop_loss = new_stop_loss
        if not isinstance(new_take_profit, _UnsetType):
            position.take_profit = new_take_profit

        return ModificationResult(success=True)

    def _validate_sl_tp_levels(
        self,
        direction: OrderDirection,
        bid: float,
        ask: float,
        stop_loss: Optional[float],
        take_profit: Optional[float]
    ) -> Optional[ModificationRejectionReason]:
        """
        Validate SL/TP levels against current prices and each other.

        Args:
            direction: Position direction (LONG/SHORT)
            bid: Current bid price
            ask: Current ask price
            stop_loss: Stop loss level to validate (None = no SL)
            take_profit: Take profit level to validate (None = no TP)

        Returns:
            None if valid, ModificationRejectionReason if invalid
        """
        if stop_loss is not None:
            if direction == OrderDirection.LONG and stop_loss >= bid:
                self._logger.warning(
                    f"Invalid SL for LONG: sl={stop_loss} >= bid={bid}")
                return ModificationRejectionReason.INVALID_SL_LEVEL
            if direction == OrderDirection.SHORT and stop_loss <= ask:
                self._logger.warning(
                    f"Invalid SL for SHORT: sl={stop_loss} <= ask={ask}")
                return ModificationRejectionReason.INVALID_SL_LEVEL

        if take_profit is not None:
            if direction == OrderDirection.LONG and take_profit <= bid:
                self._logger.warning(
                    f"Invalid TP for LONG: tp={take_profit} <= bid={bid}")
                return ModificationRejectionReason.INVALID_TP_LEVEL
            if direction == OrderDirection.SHORT and take_profit >= ask:
                self._logger.warning(
                    f"Invalid TP for SHORT: tp={take_profit} >= ask={ask}")
                return ModificationRejectionReason.INVALID_TP_LEVEL

        # SL and TP must not cross each other
        if stop_loss is not None and take_profit is not None:
            if direction == OrderDirection.LONG and stop_loss >= take_profit:
                self._logger.warning(
                    f"SL/TP cross for LONG: sl={stop_loss} >= tp={take_profit}")
                return ModificationRejectionReason.SL_TP_CROSS
            if direction == OrderDirection.SHORT and stop_loss <= take_profit:
                self._logger.warning(
                    f"SL/TP cross for SHORT: sl={stop_loss} <= tp={take_profit}")
                return ModificationRejectionReason.SL_TP_CROSS

        return None

    def mark_dirty(self, tick: TickData):
        """
        Mark positions as needing update (LAZY EVALUATION).

        Args:
            tick: Current tick data with bid/ask prices
        """
        self._positions_dirty = True
        self._current_tick = tick
        self._current_prices[tick.symbol] = (tick.bid, tick.ask)

    def _calculate_tick_value(
        self,
        symbol_spec: SymbolSpecification,
        current_price: float
    ) -> float:
        """
        Calculate tick_value dynamically.

        Args:
            symbol_spec: Static symbol specification
            current_price: Current market price

        Returns:
            tick_value for P&L calculations
        """
        # Quote Currency matches Account Currency
        if self.account_currency == symbol_spec.quote_currency:
            self._last_conversion_rate = None
            return 1.0

        # Base Currency matches Account Currency
        elif self.account_currency == symbol_spec.base_currency:
            if current_price <= 0:
                raise ValueError(
                    f"Invalid price for tick_value calculation: {current_price}"
                )
            self._last_conversion_rate = current_price
            return 1.0 / current_price

        # Cross Currency - Not supported
        else:
            raise NotImplementedError(
                f"Cross-currency conversion not supported: "
                f"Account: {self.account_currency}, "
                f"Symbol: {symbol_spec.symbol} "
                f"(Base: {symbol_spec.base_currency}, Quote: {symbol_spec.quote_currency})"
            )

    def _ensure_positions_updated(self) -> None:
        """
        Ensure positions are updated with latest prices (LAZY EVALUATION).

        Performance:
        - Only updates if _positions_dirty = True
        - Builds symbol specs on-demand (not every tick!)
        """
        if not self._positions_dirty:
            return

        # Update all positions with current prices
        for position in self.open_positions.values():
            symbol = position.symbol

            # Skip if no price data for this symbol yet
            if symbol not in self._current_prices:
                continue

            # Get cached symbol spec (BrokerConfig already caches!)
            spec = self.broker_config.get_symbol_specification(symbol)

            # Calculate tick_value
            bid, ask = self._current_prices[symbol]
            current_price = (bid + ask) / 2.0
            tick_value = self._calculate_tick_value(spec, current_price)

            # Update position P&L
            position.update_current_price(
                bid=bid,
                ask=ask,
                tick_value=tick_value,
                digits=spec.digits
            )

        self._positions_dirty = False

    def get_open_positions(self) -> List[Position]:
        """Get list of all open positions"""
        return list(self.open_positions.values())

    def get_trade_history(self) -> List[TradeRecord]:
        """
        Get list of all completed trades.

        Returns:
            List of TradeRecord objects with full audit trail
        """
        return list(self._trade_history)

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get specific position by ID"""
        return self.open_positions.get(position_id)

    def has_open_positions(self) -> bool:
        """Check if any positions are open"""
        return len(self.open_positions) > 0

    # ============================================
    # Account Information - for example, for decision
    # ============================================

    def get_account_info(self, order_direction: OrderDirection) -> AccountInfo:
        """
        Get current account information.

        Returns account state including:
        - balance: Total account balance
        - equity: Balance + unrealized P&L
        - margin_used: Margin locked in open positions
        - free_margin: Available margin for new trades
        - margin_level: (equity / margin_used) * 100

        Returns:
            AccountInfo dataclass with all account metrics
        """
        # Ensure positions have latest prices (lazy update)
        self._ensure_positions_updated()

        # Calculate total unrealized P&L
        unrealized_pnl = sum(
            pos.unrealized_pnl for pos in self.open_positions.values()
        )

        # Equity = Balance + Unrealized P&L
        equity = self.balance + unrealized_pnl

        # Calculate margin used (delegated to broker adapter)
        margin_used = 0.0
        if self._current_tick is not None:
            for pos in self.open_positions.values():
                position_margin = self.broker_config.calculate_margin(
                    pos.symbol, pos.lots, self._current_tick, order_direction
                )
                margin_used += position_margin

        # Free margin (calculated AFTER loop!)
        free_margin = equity - margin_used

        # Margin level
        if margin_used > 0:
            margin_level = (equity / margin_used) * 100
        else:
            margin_level = 0.0

        # Position stats
        total_lots = sum(pos.lots for pos in self.open_positions.values())

        return AccountInfo(
            balance=self.balance,
            equity=equity,
            margin_used=margin_used,
            free_margin=free_margin,
            margin_level=margin_level,
            open_positions=len(self.open_positions),
            total_lots=total_lots,
            currency=self.account_currency,
            leverage=self.leverage
        )

    def get_total_trades(self):
        return self._total_trades

    def get_winning_trades(self):
        return self._winning_trades

    def get_losing_trades(self):
        return self._losing_trades

    def _calculate_equity(self) -> float:
        """Get current equity (balance + unrealized P&L)"""
        unrealized_pnl = sum(
            pos.unrealized_pnl for pos in self.open_positions.values()
        )
        return self.balance + unrealized_pnl

    def get_free_margin(self, order_direction: OrderDirection) -> float:
        """Get free margin"""
        account = self.get_account_info(order_direction)
        return account.free_margin

    # ============================================
    # Cost Tracking ( & TYPED)
    # ============================================

    def get_cost_breakdown(self) -> CostBreakdown:
        """
        Get breakdown of all trading costs.

        Returns immutable copy (safe for external use).

        Returns:
            CostBreakdown with total_spread_cost, total_commission, total_swap, total_fees
        """
        return replace(self._cost_tracking)

    # ============================================
    # Statistics ( & TYPED)
    # ============================================

    def _update_statistics(self, position: Position, realized_pnl: float) -> None:
        """
        Update trading statistics after position close.

        Uses direct attributes instead of dict.
        """
        self._total_trades += 1

        if position.direction == OrderDirection.LONG:
            self._total_long_trades += 1
        if position.direction == OrderDirection.SHORT:
            self._total_short_trades += 1

        if realized_pnl > 0:
            self._winning_trades += 1
            self._total_profit += realized_pnl
        else:
            self._losing_trades += 1
            self._total_loss += abs(realized_pnl)

        # Update max equity
        equity = self._calculate_equity()
        if equity > self._max_equity:
            self._max_equity = equity

        # Update max drawdown
        drawdown = self._max_equity - equity
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown

    def get_portfolio_statistics(self) -> PortfolioStats:
        """
        Get portfolio statistics with fee breakdown.

        Creates new PortfolioStats from direct attributes.
        Always returns new object (safe for external use).
        """
        # Ensure positions have latest values for accurate stats
        self._ensure_positions_updated()

        # Calculate win rate
        if self._total_trades > 0:
            win_rate = self._winning_trades / self._total_trades
        else:
            win_rate = 0.0

        # Calculate profit factor
        if self._total_loss > 0:
            profit_factor = self._total_profit / self._total_loss
        else:
            profit_factor = 0.0 if self._total_profit == 0 else float('inf')

        return PortfolioStats(
            total_trades=self._total_trades,
            total_long_trades=self._total_long_trades,
            total_short_trades=self._total_short_trades,
            winning_trades=self._winning_trades,
            losing_trades=self._losing_trades,
            total_profit=self._total_profit,
            total_loss=self._total_loss,
            max_drawdown=self._max_drawdown,
            max_equity=self._max_equity,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_spread_cost=self._cost_tracking.total_spread_cost,
            total_commission=self._cost_tracking.total_commission,
            total_swap=self._cost_tracking.total_swap,
            total_fees=self._cost_tracking.total_fees,
            currency=self.account_currency,  # Account currency
            broker_name=self.broker_config.get_broker_name(),
            broker_type=self.broker_config.broker_type,
            current_conversion_rate=self._last_conversion_rate,
            current_balance=self.balance,
            initial_balance=self.initial_balance
        )

    def reset(self) -> None:
        """Reset portfolio to initial state"""
        self.balance = self.initial_balance
        self.realized_pnl = 0.0
        self.open_positions.clear()
        self._trade_history.clear()
        self._position_counter = 0

        # Reset cost tracking object
        self._cost_tracking = CostBreakdown()

        # Reset direct attributes
        self._total_trades = 0
        self._total_long_trades = 0
        self._total_short_trades = 0
        self._winning_trades = 0
        self._losing_trades = 0
        self._total_profit = 0.0
        self._total_loss = 0.0
        self._max_drawdown = 0.0
        self._max_equity = self.initial_balance

    def _log_trade_record(self, record: TradeRecord) -> None:
        """
        Log trade record details for debugging.

        Args:
            record: Completed TradeRecord to log
        """
        duration_ticks = record.exit_tick_index - record.entry_tick_index

        self._logger.debug(
            f"📊 TRADE RECORD #{len(self._trade_history)} | {record.position_id}\n"
            f"   {record.symbol} {record.direction} {record.lots} lots\n"
            f"   Entry: {record.entry_price:.5f} @ tick {record.entry_tick_index} | "
            f"Exit: {record.exit_price:.5f} @ tick {record.exit_tick_index} | "
            f"Duration: {duration_ticks} ticks\n"
            f"   Tick Value: entry={record.entry_tick_value:.5f}, exit={record.exit_tick_value:.5f}\n"
            f"   Gross P&L: {record.gross_pnl:.2f} | Fees: {record.total_fees:.2f} "
            f"(spread={record.spread_cost:.2f}) | Net P&L: {record.net_pnl:.2f} {record.account_currency}"
        )
