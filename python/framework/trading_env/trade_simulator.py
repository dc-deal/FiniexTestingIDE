# ============================================
# python/framework/trading_env/trade_simulator.py
# ============================================
"""
FiniexTestingIDE - Trade Simulator
Simulates broker trading environment with realistic execution

Core Responsibilities:
- Order execution with realistic delays (MVP: tick-based)
- Portfolio management (positions, P&L)
- Price updates and spread calculations
- Trading fee simulation
- Account queries for Decision Logic

Architecture:
TradeSimulator = OrderExecutionEngine + PortfolioManager + BrokerConfig
- Engine: Handles order delays (PENDING state)
- Portfolio: Manages positions and balance
- Broker: Provides spreads, symbols, capabilities
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from python.framework.types import TickData
from .broker_config import BrokerConfig
from .portfolio_manager import PortfolioManager, Position, AccountInfo
from .order_execution_engine import OrderExecutionEngine
from .order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    OrderResult,
    RejectionReason,
    MarketOrder,
    LimitOrder,
    create_rejection_result
)
from .trading_fees import create_spread_fee_from_tick


class TradeSimulator:
    """
    Trade Simulator - Orchestrates order execution and portfolio.

    Uses OrderExecutionEngine for realistic delays (Issue #003).
    Orders go through PENDING → EXECUTED lifecycle.
    """

    def __init__(
        self,
        broker_config: BrokerConfig,
        initial_balance: float = 10000,
        currency: str = "EUR",
        seeds: Optional[Dict[str, int]] = None
    ):
        """
        Initialize trade simulator.

        Args:
            broker_config: Broker configuration with spreads and capabilities
            initial_balance: Starting account balance
            currency: Account currency
            seeds: Seeds for order execution delays (from config)
        """
        self.broker = broker_config

        # Create portfolio manager
        leverage = self.broker.get_max_leverage()

        self.portfolio = PortfolioManager(
            initial_balance=initial_balance,
            currency=currency,
            leverage=leverage
        )
        # Current market prices
        # symbol: (bid, ask)
        self._current_prices: Dict[str, Tuple[float, float]] = {}

        # Order execution engine with deterministic delays
        seeds = seeds or {}
        self.execution_engine = OrderExecutionEngine(seeds)

        # Internal state
        self._order_counter = 0

        # EXTENDED: Order history (all orders)
        self._order_history: List[OrderResult] = []
        self._current_tick: Optional[TickData] = None
        self._tick_counter = 0

        # Execution statistics
        self._execution_stats = {
            "orders_sent": 0,
            "orders_executed": 0,
            "orders_rejected": 0,
            "total_commission": 0.0,
            "total_spread_cost": 0.0,
        }

    # ============================================
    # Price Updates
    # ============================================

    def update_prices(self, tick: TickData):
        """
        Update prices and process pending orders.

        Called by BatchOrchestrator on every tick to:
        1. Update current tick data
        2. Process pending orders that are ready to fill
        3. Update portfolio with new prices (unrealized P&L)

        Args:
            tick: Current tick data with bid/ask prices
        """
        self._current_tick = tick
        self._tick_counter += 1

        # Process pending orders from execution engine
        filled_orders = self.execution_engine.process_tick(self._tick_counter)

        for pending_order in filled_orders:
            self._fill_pending_order(pending_order)

        self._current_prices[tick.symbol] = (tick.bid, tick.ask)

        # Update all positions
        symbol_specs = {
            sym: self.broker.get_symbol_info(sym)
            for sym in self._current_prices.keys()
        }

        self.portfolio.update_positions(self._current_prices, symbol_specs)

    def get_current_price(self, symbol: str) -> Tuple[float, float]:
        """Get current bid/ask prices"""
        if symbol not in self._current_prices:
            raise ValueError(f"No price data available for {symbol}")

        return self._current_prices[symbol]

    def get_current_price(self, symbol: str) -> tuple[float, float]:
        """
        Get current bid/ask price for symbol.

        Returns:
            (bid, ask) tuple
        """
        if not self._current_tick or self._current_tick.symbol != symbol:
            raise ValueError(f"No current price data for {symbol}")

        return self._current_tick.bid, self._current_tick.ask

    # ============================================
    # Order Execution
    # ============================================

    def send_order(
        self,
        symbol: str,
        order_type: OrderType,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> OrderResult:
        """
        Send order to broker simulation.

        EXTENDED: Automatically attaches SpreadFee from live tick data.
        """
        self._execution_stats["orders_sent"] += 1

        # Generate order ID
        self._order_counter += 1
        order_id = f"order_{self._order_counter}"

        # Validate order
        is_valid, error = self.broker.validate_order(symbol, lots)
        if not is_valid:
            self._execution_stats["orders_rejected"] += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.INVALID_LOT_SIZE,
                message=error
            )
            self._order_history.append(result)
            return result

        # Check symbol tradeable
        if not self.broker.is_symbol_tradeable(symbol):
            self._execution_stats["orders_rejected"] += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.SYMBOL_NOT_TRADEABLE,
                message=f"Symbol {symbol} not tradeable"
            )
            self._order_history.append(result)
            return result

        # Execute based on order type
        if order_type == OrderType.MARKET:
            result = self._execute_market_order(
                order_id, symbol, direction, lots, **kwargs)
        elif order_type == OrderType.LIMIT:
            result = self._execute_limit_order(
                order_id, symbol, direction, lots, **kwargs)
        else:
            # Extended orders - MVP: Not implemented
            self._execution_stats["orders_rejected"] += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
                message=f"Order type {order_type} not implemented in MVP"
            )

        # EXTENDED: Store in order history
        self._order_history.append(result)

        return result

    def _execute_market_order(
        self,
        order_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> OrderResult:
        """
        Execute market order with delay.

        Submits to execution engine, returns PENDING status immediately.
        """
        # Pre-validate margin
        estimated_margin = lots * 100000 * 0.01
        if self.portfolio.get_free_margin() < estimated_margin:
            self._execution_stats["orders_rejected"] += 1
            return create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.INSUFFICIENT_MARGIN,
                message=f"Insufficient margin: need ~{estimated_margin:.2f}"
            )

        # Submit to execution engine
        engine_order_id = self.execution_engine.submit_order(
            symbol=symbol,
            direction=direction.value,
            lots=lots,
            current_tick=self._tick_counter,
            **kwargs
        )

        # Return PENDING status
        return OrderResult(
            order_id=engine_order_id,
            status=OrderStatus.PENDING,
            metadata={
                "symbol": symbol,
                "direction": direction.value,
                "lots": lots,
                "submitted_at_tick": self._tick_counter
            }
        )

    def _fill_pending_order(self, pending_order) -> None:
        """
        Fill pending order that has completed its delay.

        Called by update_prices() for orders from execution engine.

        Args:
            pending_order: PendingOrder from execution engine
        """
        # Get current prices
        bid, ask = self.get_current_price(pending_order.symbol)

        # Determine entry price
        if pending_order.direction == "BUY":
            entry_price = ask
        else:
            entry_price = bid

        # Calculate spread fee
        # EXTENDED: Create SpreadFee from LIVE tick data
        symbol_info = self.broker.get_symbol_info(pending_order.symbol)
        tick_value = symbol_info.get('tick_value', 1.0)
        digits = symbol_info.get('digits', 5)
        spread_fee = create_spread_fee_from_tick(
            tick=self._current_tick,
            lots=pending_order.lots,
            tick_value=tick_value,
            digits=digits
        )
        self._execution_stats["total_spread_cost"] += spread_fee.cost
        # Check margin available
        margin_required = self.broker.calculate_margin(
            pending_order.symbol, pending_order.lots)
        free_margin = self.portfolio.get_free_margin()

        if margin_required > free_margin:
            self._execution_stats["orders_rejected"] += 1
            return create_rejection_result(
                order_id=pending_order.order_id,
                reason=RejectionReason.INSUFFICIENT_MARGIN,
                message=f"Required margin {margin_required:.2f} exceeds free margin {free_margin:.2f}"
            )

        # Create position in portfolio
        position = self.portfolio.open_position(
            symbol=pending_order.symbol,
            direction=pending_order.direction,
            lots=pending_order.lots,
            entry_price=entry_price,
            entry_fee=spread_fee,
            stop_loss=pending_order.order_kwargs.get('stop_loss'),
            take_profit=pending_order.order_kwargs.get('take_profit'),
            comment=pending_order.order_kwargs.get('comment', ''),
            magic_number=pending_order.order_kwargs.get('magic_number', 0)
        )

        # Create successful order result
        result = OrderResult(
            order_id=pending_order.order_id,
            status=OrderStatus.EXECUTED,
            executed_price=entry_price,
            executed_lots=pending_order.lots,
            execution_time=datetime.now(),
            commission=0.0,
            broker_order_id=position.position_id,
            metadata={
                "symbol": pending_order.symbol,
                "direction": pending_order.direction,
                "position_id": position.position_id,
                "spread_cost": spread_fee.cost,
                "spread_points": spread_fee.metadata['spread_points'],
                "filled_at_tick": self._tick_counter
            }
        )

        # Update statistics
        self._execution_stats["orders_executed"] += 1
        self._order_history.append(result)

    def _execute_limit_order(
        self,
        order_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> OrderResult:
        """Execute limit order (MVP: Not implemented)"""
        self._execution_stats["orders_rejected"] += 1
        return create_rejection_result(
            order_id=order_id,
            reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
            message="Limit orders not implemented in MVP"
        )

    # ============================================
    # Position Management (EXTENDED)
    # ============================================

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
            new_stop_loss: New SL (None = no change)
            new_take_profit: New TP (None = no change)

        Returns:
            True if modified successfully

        Example:
            # Trailing stop
            position = trading_env.get_position("pos_1")
            if tick.bid > position.entry_price + 0.0050:
                trading_env.modify_position(
                    position_id="pos_1",
                    new_stop_loss=position.entry_price + 0.0020
                )
        """
        return self.portfolio.modify_position(
            position_id=position_id,
            new_stop_loss=new_stop_loss,
            new_take_profit=new_take_profit
        )

    def close_position(
        self,
        position_id: str,
        lots: Optional[float] = None
    ) -> OrderResult:
        """Close position (full or partial)"""
        try:
            position = self.portfolio.get_position(position_id)
            if not position:
                return create_rejection_result(
                    order_id=f"close_{position_id}",
                    reason=RejectionReason.BROKER_ERROR,
                    message=f"Position {position_id} not found"
                )

            # Get current price
            bid, ask = self.get_current_price(position.symbol)

            # Determine close price
            if position.direction == OrderDirection.BUY:
                close_price = bid
            else:
                close_price = ask

            # Close position
            realized_pnl = self.portfolio.close_position(
                position_id=position_id,
                exit_price=close_price,
                exit_fee=None  # MVP: No exit commission
            )

            result = OrderResult(
                order_id=f"close_{position_id}",
                status=OrderStatus.EXECUTED,
                executed_price=close_price,
                executed_lots=lots if lots else position.lots,
                execution_time=datetime.now(),
                commission=0.0,
                metadata={
                    "realized_pnl": realized_pnl,
                    "position_id": position_id
                }
            )

            # EXTENDED: Add to order history
            self._order_history.append(result)

            return result

        except Exception as e:
            return create_rejection_result(
                order_id=f"close_{position_id}",
                reason=RejectionReason.BROKER_ERROR,
                message=str(e)
            )

    # ============================================
    # Account Queries
    # ============================================

    def get_account_info(self) -> AccountInfo:
        """Get current account information"""
        return self.portfolio.get_account_info()

    def get_open_positions(self) -> List[Position]:
        """Get all open positions"""
        return self.portfolio.get_open_positions()

    def get_pending_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get list of pending orders waiting for execution.

        Returns orders that are submitted but not yet filled.
        This is CRITICAL for preventing duplicate order submissions.

        Args:
            symbol: Filter by symbol (None = all pending orders)

        Returns:
            List of pending order info dicts with:
            - order_id: Unique identifier
            - symbol: Trading symbol
            - direction: "BUY" or "SELL"
            - lots: Position size
            - placed_at_tick: When order was submitted
            - fill_at_tick: When order will be executed
            - ticks_remaining: Ticks until execution

        Example:
            pending = self.get_pending_orders("EURUSD")
            if len(pending) >= 5:
                return None  # Too many pending orders
        """
        pending_list = []

        for order_id, pending_order in self.execution_engine.pending_orders.items():
            # Filter by symbol if specified
            if symbol and pending_order.symbol != symbol:
                continue

            # Calculate remaining ticks
            ticks_remaining = pending_order.fill_at_tick - self._tick_counter

            pending_list.append({
                "order_id": order_id,
                "symbol": pending_order.symbol,
                "direction": pending_order.direction,
                "lots": pending_order.lots,
                "placed_at_tick": pending_order.placed_at_tick,
                "fill_at_tick": pending_order.fill_at_tick,
                "ticks_remaining": max(0, ticks_remaining)
            })

        return pending_list

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get specific position by ID"""
        return self.portfolio.get_position(position_id)

    def get_balance(self) -> float:
        """Get account balance"""
        return self.portfolio.balance

    def get_equity(self) -> float:
        """Get account equity"""
        return self.portfolio.get_equity()

    def get_free_margin(self) -> float:
        """Get free margin"""
        return self.portfolio.get_free_margin()

    # ============================================
    # Order History (EXTENDED)
    # ============================================

    def get_order_history(self) -> List[OrderResult]:
        """
        Get complete order history.

        NEW METHOD for performance analysis and debugging.

        Returns:
            List of all OrderResults (executed and rejected)

        Example:
            history = trading_env.get_order_history()

            executed = [o for o in history if o.is_success]
            rejected = [o for o in history if o.is_rejected]

            print(f"Total orders: {len(history)}")
            print(f"Executed: {len(executed)}")
            print(f"Rejected: {len(rejected)}")
        """
        return self._order_history.copy()

    def get_closed_positions(self) -> List[Position]:
        """
        Get all closed positions.

        NEW METHOD for performance reports and P&L analysis.

        Returns:
            List of closed Position objects with full fee breakdown

        Example:
            closed = trading_env.get_closed_positions()

            for pos in closed:
                print(f"Position {pos.position_id}:")
                print(f"  P&L: {pos.unrealized_pnl:.2f}")
                print(f"  Spread: {pos.get_spread_cost():.2f}")
                print(f"  Duration: {pos.close_time - pos.entry_time}")
        """
        return self.portfolio.get_closed_positions()

    # ============================================
    # Broker Information
    # ============================================

    def get_broker_name(self) -> str:
        """Get broker name"""
        return self.broker.get_broker_name()

    def get_broker_capabilities(self):
        """Get broker order capabilities"""
        return self.broker.get_order_capabilities()

    def get_symbol_info(self, symbol: str) -> Dict:
        """Get symbol specifications"""
        return self.broker.get_symbol_info(symbol)

    # ============================================
    # Statistics (EXTENDED)
    # ============================================

    def get_execution_stats(self) -> Dict:
        """
        Get order execution statistics.

        EXTENDED: Now includes total_spread_cost.
        """
        return self._execution_stats.copy()

    def get_portfolio_stats(self) -> Dict:
        """
        Get portfolio performance statistics with fee breakdown.

        EXTENDED: Includes spread/commission/swap costs.
        """
        return self.portfolio.get_statistics()

    def get_cost_breakdown(self) -> Dict:
        """
        Get detailed cost breakdown.

        NEW METHOD for cost analysis.

        Returns:
            Dict with total_spread_cost, total_commission, total_swap
        """
        return self.portfolio.get_cost_breakdown()

    def reset(self):
        """Reset simulator to initial state"""
        self.portfolio.reset()
        self._current_prices.clear()
        self._order_counter = 0
        self._order_history.clear()  # EXTENDED

        self._execution_stats = {
            "orders_sent": 0,
            "orders_executed": 0,
            "orders_rejected": 0,
            "total_commission": 0.0,
            "total_spread_cost": 0.0,
        }

    def __repr__(self) -> str:
        """String representation"""
        account = self.get_account_info()
        return (
            f"TradeSimulator("
            f"broker='{self.broker.get_broker_name()}', "
            f"balance={account.balance:.2f}, "
            f"equity={account.equity:.2f}, "
            f"positions={account.open_positions}"
            f")"
        )
