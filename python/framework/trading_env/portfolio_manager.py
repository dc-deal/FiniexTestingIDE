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

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum

from python.framework.types.broker_types import DynamicSymbolData, SymbolSpecification

from ..types.order_types import OrderDirection
from .trading_fees import AbstractTradingFee, SpreadFee, SwapFee, CommissionFee
from python.framework.types.trading_env_types import AccountInfo, PortfolioStats, CostBreakdown


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
        from .trading_fees import FeeType
        spread_fees = self.get_fees_by_type(FeeType.SPREAD)
        return sum(fee.cost for fee in spread_fees)

    def get_commission_cost(self) -> float:
        """Get total commission cost"""
        from .trading_fees import FeeType
        comm_fees = self.get_fees_by_type(FeeType.COMMISSION)
        return sum(fee.cost for fee in comm_fees)

    def get_swap_cost(self) -> float:
        """Get total swap cost"""
        from .trading_fees import FeeType
        swap_fees = self.get_fees_by_type(FeeType.SWAP)
        return sum(fee.cost for fee in swap_fees)

    def get_margin_used(self, contract_size: float, leverage: int) -> float:
        """Calculate margin used by this position"""
        return (self.lots * contract_size * self.entry_price) / leverage

    @property
    def is_open(self) -> bool:
        """Check if position is still open"""
        return self.status == PositionStatus.OPEN


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
        initial_balance: float,
        account_currency: str = "USD",  # Changed from 'currency'
        leverage: int = 100,
        margin_call_level: float = 50.0,
        stop_out_level: float = 20.0
    ):
        """
        Initialize portfolio manager.

        Args:
            initial_balance: Starting balance
            account_currency: Account currency (e.g., "USD", "EUR", "JPY")
            leverage: Account leverage
            margin_call_level: Margin call threshold percentage
            stop_out_level: Stop out threshold percentage
        """
        self.initial_balance = initial_balance
        self.account_currency = account_currency  # Changed from 'currency'
        self.leverage = leverage
        self.margin_call_level = margin_call_level
        self.stop_out_level = stop_out_level

        # Account state
        self.balance = initial_balance
        self.realized_pnl = 0.0

        # Positions
        self.open_positions: Dict[str, Position] = {}
        self.closed_positions: List[Position] = []

        # Position counter
        self._position_counter = 0

        # Cost tracking as CostBreakdown object
        self._cost_tracking = CostBreakdown()

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

    # ============================================
    # Position Management
    # ============================================

    def open_position(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        entry_price: float,
        entry_fee: Optional[AbstractTradingFee] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "",
        magic_number: int = 0
    ) -> Position:
        """
        Open new position.

        Accepts entry_fee (typically SpreadFee).
        """
        # Generate position ID
        self._position_counter += 1
        position_id = f"pos_{symbol.lower()}_{self._position_counter}"

        # Create position
        position = Position(
            position_id=position_id,
            symbol=symbol,
            direction=direction,
            lots=lots,
            entry_price=entry_price,
            entry_time=datetime.now(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            comment=comment,
            magic_number=magic_number
        )

        # Attach entry fee
        if entry_fee:
            position.add_fee(entry_fee)

            # Update cost tracking object
            from .trading_fees import FeeType
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
        exit_fee: Optional[AbstractTradingFee] = None
    ) -> float:
        """
        Close position and realize P&L.

        Accepts exit_fee (commission or final swap).
        """
        if position_id not in self.open_positions:
            raise ValueError(f"Position {position_id} not found")

        position = self.open_positions[position_id]

        # Add exit fee if provided
        if exit_fee:
            position.add_fee(exit_fee)

            # Update cost tracking
            from .trading_fees import FeeType
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
        position.close_time = datetime.now()
        position.close_price = exit_price

        # Move to closed positions
        self.closed_positions.append(position)
        del self.open_positions[position_id]

        # Update statistics
        self._update_statistics(position, realized_pnl)

        return realized_pnl

    def modify_position(
        self,
        position_id: str,
        new_stop_loss: Optional[float] = None,
        new_take_profit: Optional[float] = None
    ) -> bool:
        """
        Modify position SL/TP.

        NEW METHOD for dynamic position management.

        Args:
            position_id: Position to modify
            new_stop_loss: New stop loss (None = no change)
            new_take_profit: New take profit (None = no change)

        Returns:
            True if modified successfully
        """
        if position_id not in self.open_positions:
            return False

        position = self.open_positions[position_id]

        if new_stop_loss is not None:
            position.stop_loss = new_stop_loss

        if new_take_profit is not None:
            position.take_profit = new_take_profit

        return True

    def update_positions(
        self,
        current_prices: Dict[str, tuple[float, float]],
        symbol_specs: Dict[str, DynamicSymbolData]
    ) -> None:
        '''
        Update all open positions with current prices.

        Args:
            current_prices: Dict[symbol, (bid, ask)]
            symbol_specs: Dict[symbol, DynamicSymbolData] with:
                - specification: Static SymbolSpecification
                - tick_value: Dynamic value calculated by TradeSimulator

        Note:
            tick_value is calculated dynamically by TradeSimulator based on
            account currency and current market price. It is NOT the static
            value from broker config.
        '''
        for position in self.open_positions.values():
            if position.symbol in current_prices:
                bid, ask = current_prices[position.symbol]
                dynamic_data = symbol_specs.get(position.symbol)

                if dynamic_data is None:
                    continue  # Skip if no data available

                # Extract from typed object
                tick_value = dynamic_data.tick_value
                digits = dynamic_data.digits

                position.update_current_price(bid, ask, tick_value, digits)

    def get_open_positions(self) -> List[Position]:
        """Get list of all open positions"""
        return list(self.open_positions.values())

    def get_closed_positions(self) -> List[Position]:
        """
        Get list of all closed positions.

        NEW METHOD for order history and performance analysis.
        """
        return self.closed_positions.copy()

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get specific position by ID"""
        return self.open_positions.get(position_id)

    def has_open_positions(self) -> bool:
        """Check if any positions are open"""
        return len(self.open_positions) > 0

    # ============================================
    # Account Information
    # ============================================

    def get_account_info(self) -> AccountInfo:
        """
        Get current account information.

        Always returns copy (safe for external use).
        """
        # Calculate total unrealized P&L
        unrealized_pnl = sum(
            pos.unrealized_pnl for pos in self.open_positions.values()
        )

        # Equity = Balance + Unrealized P&L
        equity = self.balance + unrealized_pnl

        # Calculate margin used
        margin_used = sum(
            pos.lots * 100000 * pos.entry_price / self.leverage
            for pos in self.open_positions.values()
        )

        # Free margin
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
            currency=self.account_currency,  # Changed from 'currency'
            leverage=self.leverage
        )

    def get_total_trades(self):
        return self._total_trades

    def get_winning_trades(self):
        return self._winning_trades

    def get_losing_trades(self):
        return self._losing_trades

    def get_equity(self) -> float:
        """Get current equity (balance + unrealized P&L)"""
        unrealized_pnl = sum(
            pos.unrealized_pnl for pos in self.open_positions.values()
        )
        return self.balance + unrealized_pnl

    def get_free_margin(self) -> float:
        """Get free margin"""
        account = self.get_account_info()
        return account.free_margin

    def is_margin_call(self) -> bool:
        """Check if margin call"""
        account = self.get_account_info()
        if account.margin_used == 0:
            return False
        return account.margin_level < self.margin_call_level

    def is_stop_out(self) -> bool:
        """Check if stop out"""
        account = self.get_account_info()
        if account.margin_used == 0:
            return False
        return account.margin_level < self.stop_out_level

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
        equity = self.get_equity()
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
            currency=self.account_currency  # Account currency
        )

    def reset(self) -> None:
        """Reset portfolio to initial state"""
        self.balance = self.initial_balance
        self.realized_pnl = 0.0
        self.open_positions.clear()
        self.closed_positions.clear()
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
