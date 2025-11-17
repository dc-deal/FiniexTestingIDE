# ============================================
# python/framework/trading_env/trade_simulator.py
# ============================================
"""
FiniexTestingIDE - Trade Simulator ()
Simulates broker trading environment with realistic execution

 CHANGES:
- Direct attributes for execution stats (_orders_sent, _orders_executed, etc.)
- Always-copy public API (using replace())
- Cleaner, more maintainable code structure
- FULLY TYPED: All statistics methods return dataclasses (no more dicts!)
- CURRENCY: account_currency with auto-detection from symbol
"""
from datetime import datetime
import json
from typing import Optional, List, Dict, Tuple

from python.components.logger.abstract_logger import AbstractLogger
from python.framework.trading_env.order_latency_simulator import OrderLatencySimulator
from python.framework.types.broker_types import SymbolSpecification
from python.framework.types.latency_simulator_types import PendingOrder, PendingOrderAction
from python.framework.types.market_data_types import TickData
from python.framework.types.trading_env_types import AccountInfo, ExecutionStats
from python.framework.utils.trade_simulator_utils import pending_order_to_position
from .broker_config import BrokerConfig
from .portfolio_manager import PortfolioManager, Position
from ..types.order_types import (
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

    Uses direct attributes for execution statistics.

    CURRENCY HANDLING:
    - Supports "auto" detection: account_currency = symbol quote currency
    - Logs currency operations for transparency
    """

    def __init__(
        self,
        broker_config: BrokerConfig,
        initial_balance: float,
        account_currency: str,  # Changed from 'currency', supports "auto"
        symbol: str,  # NEW: Required for auto-detection
        logger: AbstractLogger,
        seeds: Optional[Dict[str, int]] = None,
    ):
        """
        Initialize trade simulator.

        Args:
            broker_config: Broker configuration with spreads and capabilities
            initial_balance: Starting account balance
            account_currency: Account currency (or "auto" for symbol-based detection)
            symbol: Trading symbol (required for auto-detection)
            logger: Logger instance
            seeds: Seeds for order execution delays (from config)
        """
        self.broker = broker_config
        self.logger = logger

        # === CURRENCY AUTO-DETECTION ===
        # If account_currency is "auto", extract from symbol (last 3 chars)
        if account_currency == "auto":
            # Validate symbol format
            if len(symbol) != 6:
                raise ValueError(
                    f"Invalid symbol format for auto-detection: '{symbol}'. "
                    f"Expected 6 characters (e.g., GBPUSD, EURUSD, USDJPY)"
                )

            detected_currency = symbol[-3:].upper()

            # Show broker currency vs. detected currency for transparency
            broker_spec = self.broker.get_broker_specification()

            logger.warning(
                f"💱 CURRENCY AUTO-DETECTION:\n"
                f"   Symbol: {symbol} → Detected: {detected_currency}\n"
                f"   Using: {detected_currency} (auto-detection overrides broker)\n"
                f"   All P&L calculations will be in {detected_currency}."
            )

            account_currency = detected_currency
            self.configured_account_currency = "auto"  # NEW
        else:
            # Explicit currency provided - just log it
            logger.info(
                f"💱 Account Currency: {account_currency} (explicit configuration)"
            )
            self.configured_account_currency = account_currency

        # Store final account currency
        self.account_currency = account_currency

        # Create portfolio manager with broker specifications
        broker_spec = self.broker.get_broker_specification()
        self.portfolio = PortfolioManager(
            initial_balance=initial_balance,
            account_currency=account_currency,
            broker_config=broker_config,
            leverage=broker_spec.leverage,
            margin_call_level=broker_spec.margin_call_level,
            stop_out_level=broker_spec.stopout_level,
            configured_account_currency=self.configured_account_currency
        )
        # Current market prices
        # symbol: (bid, ask)
        self._current_prices: Dict[str, Tuple[float, float]] = {}

        # Order latency simulator with deterministic delays
        seeds = seeds or {}
        self.latency_simulator = OrderLatencySimulator(
            seeds, logger
        )

        # Internal state
        self._order_counter = 0

        # Order history (all orders)
        self._order_history: List[OrderResult] = []
        self._current_tick: Optional[TickData] = None
        self._tick_counter = 0

        # Execution statistics as direct attributes
        self._orders_sent = 0
        self._orders_executed = 0
        self._orders_rejected = 0
        self._total_commission = 0.0
        self._total_spread_cost = 0.0

    # ============================================
    # Price Updates
    # Running every Tick
    # ============================================
    def update_prices(self, tick: TickData) -> None:
        """
        Update prices and process pending orders (OPTIMIZED).

        Called by BatchOrchestrator on every tick to:
        1. Update current tick data
        2. Process pending orders that are ready to fill
        3. Update portfolio with new tick (LAZY - no specs overhead!)

        Args:
            tick: Current tick data with bid/ask prices

        Performance Optimization:
        - BEFORE: Built symbol_specs + tick_values every tick (99.8% wasted)
        - AFTER: Just pass tick to portfolio (500× faster!)
        - Portfolio builds specs only when needed (get_account_info, close_position)
        """
        self._current_tick = tick
        self._tick_counter += 1

        # Process pending orders from latency simulator
        filled_orders = self.latency_simulator.process_tick(self._tick_counter)

        for pending_order in filled_orders:
            if pending_order.order_action == PendingOrderAction.OPEN:
                self._check_and_open_order_in_portfolio(pending_order)
            elif pending_order.order_action == PendingOrderAction.CLOSE:
                self._close_and_fill_order_in_portfolio(pending_order)

        self._current_prices[tick.symbol] = (tick.bid, tick.ask)

        # Portfolio will build symbol specs on-demand when needed
        self.portfolio.mark_dirty(tick)

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
    # Orders - Incomming
    # ============================================
    # First stage: order incommoning and submit to delay engine
    def open_order_with_latency(
        self,
        symbol: str,
        order_type: OrderType,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> OrderResult:
        """
        Send order to broker simulation.

        Automatically attaches SpreadFee from live tick data.
        Uses direct attributes for stats.
        """
        self._orders_sent += 1

        # Generate order ID
        self._order_counter += 1
        order_id = self.portfolio.get_next_position_id(symbol)

        # Some checks which don't have to be made "at the broker" (after the delay)

        # Validate order
        is_valid, error = self.broker.validate_order(symbol, lots)
        if not is_valid:
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.INVALID_LOT_SIZE,
                message=error
            )
            self._order_history.append(result)
            return result

        # Check symbol tradeable
        if not self.broker.is_symbol_tradeable(symbol):
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.SYMBOL_NOT_TRADEABLE,
                message=f"Symbol {symbol} not tradeable"
            )
            self._order_history.append(result)
            return result

        # Execute based on order type
        if order_type == OrderType.MARKET:
            # Submit to execution engine
            self.latency_simulator.submit_open_order(
                order_id=order_id,
                symbol=symbol,
                direction=direction,
                lots=lots,
                current_tick=self._tick_counter,
                **kwargs
            )
            # Return PENDING status
            result = OrderResult(
                order_id=order_id,
                status=OrderStatus.PENDING,
                metadata={
                    "symbol": symbol,
                    "direction": direction,
                    "lots": lots,
                    "submitted_at_tick": self._tick_counter
                }
            )
        else:
            # Extended orders - MVP: Not implemented
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
                message=f"Order type {order_type} not implemented in MVP"
            )

        # Store in order history
        self._order_history.append(result)

        return result

    def _check_and_open_order_in_portfolio(self, pending_order: PendingOrder) -> None:
        """
        Fill pending_order OPEN order (after delay).

        Called by update_prices when order ready.
        Creates position in portfolio.

        Args:
            pending_order: PendingOrder with order_action=PendingOrderAction.OPEN
        """
        self.logger.info(
            f"📋 Open Portfolio Position from Pending Order: {pending_order.pending_order_id} - at tick: {self._tick_counter}")
        self.logger.verbose(
            f"PENDING_ORDER_OPEN: {json.dumps(pending_order.to_dict(), indent=2)}")
        self.logger.verbose(
            f"CURRENT_TICK_AT_ORDER_OPEN: {json.dumps(self._current_tick.to_dict(), indent=2)}")
        # Get current price
        bid, ask = self.get_current_price(pending_order.symbol)

       # Determine entry price
        if pending_order.direction == OrderDirection.LONG:
            entry_price = ask
        if pending_order.direction == OrderDirection.SHORT:
            entry_price = bid

        # Calculate spread fee with DYNAMIC tick_value
        # Get static symbol specification
        symbol_spec = self.broker.get_symbol_specification(
            pending_order.symbol)
        # Calculate tick_value dynamically
        tick_value = self._calculate_tick_value(
            symbol_spec,
            self._current_tick.mid
        )
        spread_fee = create_spread_fee_from_tick(
            tick=self._current_tick,
            lots=pending_order.lots,
            tick_value=tick_value,
            digits=symbol_spec.digits
        )
        # Log tick_value calculation for transparency
        self.logger.debug(
            f"💱 tick_value calculation: "
            f"Account={self.account_currency}, "
            f"Symbol={symbol_spec.symbol} "
            f"(Base={symbol_spec.base_currency}, Quote={symbol_spec.quote_currency}), "
            f"Price={self._current_tick.mid:.5f}, "
            f"tick_value={tick_value:.5f}"
        )

        # Check margin available
        margin_required = self.broker.calculate_margin(
            pending_order.symbol, pending_order.lots, self._current_tick)
        free_margin = self.portfolio.get_free_margin()

        if margin_required > free_margin:
            self._orders_rejected += 1
            return create_rejection_result(
                order_id=pending_order.pending_order_id,
                reason=RejectionReason.INSUFFICIENT_MARGIN,
                message=f"Required margin {margin_required:.2f} exceeds free margin {free_margin:.2f}"
            )

        # Open position in portfolio
        position = self.portfolio.open_position(
            order_id=pending_order.pending_order_id,  # Pass order_id to portfolio
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

        # Create order result for history
        result = OrderResult(
            order_id=pending_order.pending_order_id,
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

        self.logger.debug(f"Open Portfolio Position finished")
        self.logger.verbose(
            f"OPEN_ORDER_RESULT: {json.dumps(result.to_dict(), indent=2)}")

        # Update statistics
        self._orders_executed += 1
        self._total_spread_cost += spread_fee.cost
        self._order_history.append(result)

    def _execute_limit_order(
        self,
        order_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> OrderResult:
        """
        Execute limit order.

        MVP: Not implemented yet.
        """
        self._orders_rejected += 1
        return create_rejection_result(
            order_id=order_id,
            reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
            message="Limit orders not implemented in MVP"
        )

    # ============================================
    # Position Management
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

    # ============================================
    # Close Commands
    # ============================================

    def close_position_with_latency(
        self,
        position_id: str,
        lots: Optional[float] = None
    ) -> OrderResult:
        """
        Submit close position order with delay.

        Position will be closed after realistic broker latency.

        Args:
            position_id: Position to close
            lots: Lots to close (None = close all)

        Returns:
            OrderResult with PENDING status
        """
        # Check if position exists
        position = self.portfolio.get_position(position_id)
        if not position:
            return create_rejection_result(
                order_id=f"close_{position_id}",
                reason=RejectionReason.BROKER_ERROR,
                message=f"Position {position_id} not found"
            )

        # Submit close order to latency simulator
        order_id = self.latency_simulator.submit_close_order(
            position_id=position_id,
            current_tick=self._tick_counter,
            close_lots=lots
        )

        # Return PENDING result (order not filled yet!)
        return OrderResult(
            order_id=order_id,
            status=OrderStatus.PENDING,
            executed_price=None,
            executed_lots=lots if lots else position.lots,
            execution_time=datetime.now(),
            commission=0.0,
            metadata={
                "position_id": position_id,
                "awaiting_fill": True
            }
        )

    def _close_and_fill_order_in_portfolio(self, pending_order: PendingOrder) -> None:
        """
        Fill pending_order CLOSE order (after delay).

        Called by update_prices when close order ready.
        Closes position in portfolio.

        Args:
            pending_order: PendingOrder with order_action=PendingOrderAction.CLOSE
        """
        self.logger.info(
            f"📋 Close and Fill Order {pending_order.pending_order_id}, at tick: {self._tick_counter}")
        self.logger.verbose(
            f"PENDING_ORDER_CLOSE_FILL: {json.dumps(pending_order.to_dict(), indent=2)}")
        self.logger.verbose(
            f"CURRENT_TICK_AT_ORDER_CLOSE_FILL: {json.dumps(self._current_tick.to_dict(), indent=2)}")

        # Get position
        position = self.portfolio.get_position(pending_order.pending_order_id)
        if not position:
            self.logger.warning(
                f"⚠️ Close order {pending_order.pending_order_id} failed: "
                f"Position {pending_order.pending_order_id} not found"
            )
            return

        # Get current price
        bid, ask = self.get_current_price(position.symbol)

        # Determine close price based on position direction
        if position.direction == OrderDirection.LONG:
            close_price = bid  # Sell at bid
        else:
            close_price = ask  # Buy back at ask

        # Close position
        realized_pnl = self.portfolio.close_position_portfolio(
            position_id=pending_order.pending_order_id,
            exit_price=close_price,
            exit_fee=None  # MVP: No exit commission
        )

        # Log close execution
        self.logger.debug(
            f"💰 Position closed: {pending_order.pending_order_id} "
            f"at {close_price:.5f}, P&L: {realized_pnl:.2f}"
        )

        # Create order result for history
        result = OrderResult(
            order_id=pending_order.pending_order_id,
            status=OrderStatus.EXECUTED,
            executed_price=close_price,
            executed_lots=pending_order.close_lots if pending_order.close_lots else position.lots,
            execution_time=datetime.now(),
            commission=0.0,
            metadata={
                "realized_pnl": realized_pnl,
                "position_id": pending_order.pending_order_id
            }
        )

        self.logger.debug(f"Close and Fill Order finished")
        self.logger.verbose(
            f"CLOSE_FILL_ORDER: {json.dumps(result.to_dict(), indent=2)}")

        self._order_history.append(result)

    def close_all_remaining_orders(self):
        """ 
            BEFORE collecting statistics - cleanup pending_order orders
        """
        # get portfolio open positions
        open_positions = self.get_open_positions()
        if open_positions:
            self.logger.warning(
                f"⚠️ {len(open_positions)} positions remain open - auto-closing"
            )
            # Make shure all open positions get a close in the latency chain.
            for pos in open_positions:
                self.close_position_with_latency(position_id=pos.position_id)

            # execute all pending positions which are CLOSE
            open_pending = self.latency_simulator.get_pending_orders()
            for pending in open_pending:
                if pending.order_action == PendingOrderAction.CLOSE:
                    self._close_and_fill_order_in_portfolio(pending)

        # clear all remaining orders ...
        # (for example recently opened orders - which did not make their way into the portfolio)
        self.latency_simulator.clear_pending()

    # ============================================
    # Account Queries
    # ============================================

    def get_account_info(self) -> AccountInfo:
        """
        Get current account information.

        Returns copy (safe for external use).
        """
        return self.portfolio.get_account_info()

    def get_open_positions(self) -> List[Position]:
        """
        Get all ACTIVE positions (real + pending OPENs, excluding pending CLOSEs).

        Decision Logic sees unified view:
        - Real positions from Portfolio
        - Pending OPEN orders as pseudo-positions (pending=True)
        - Excludes positions with pending CLOSE orders

        This hides latency simulation details from Decision Logic.

        Returns:
            List of Position objects (mix of real and pseudo-positions)
        """
        # Get real positions from portfolio
        active_positions = self.portfolio.get_open_positions()

        # Get pending OPEN orders as pseudo-positions
        pending_opens = self.latency_simulator.get_pending_orders(
            PendingOrderAction.OPEN
        )
        pseudo_positions = [
            pending_order_to_position(po) for po in pending_opens
        ]

        # Filter out positions with pending CLOSE orders
        pending_closes = self.latency_simulator.get_pending_orders(
            PendingOrderAction.CLOSE
        )
        # If Order is about to be closed, mark active_positions for algortihm.
        for p in active_positions:
            for c in pending_closes:
                if (p.position_id == c.pending_order_id):
                    p.pending = True

        # Combine: active real positions + pending opens
        return active_positions + pseudo_positions

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get specific position by ID"""
        return self.portfolio.get_position(position_id)

    def get_balance(self) -> float:
        """Get account balance"""
        return self.portfolio.balance

    def get_free_margin(self) -> float:
        """Get free margin"""
        return self.portfolio.get_free_margin()

    # ============================================
    # Dynamic Calculations"
    # ============================================
    def _calculate_tick_value(
        self,
        symbol_spec: SymbolSpecification,
        current_price: float
    ) -> float:
        """
        Calculate tick_value dynamically based on account currency and current price.

        tick_value represents the value of 1 point movement at 1 standard lot
        in the account currency.

        Calculation Logic:
        - If Account Currency == Quote Currency: tick_value = 1.0 (no conversion)
        - If Account Currency == Base Currency: tick_value = 1.0 / current_price
        - Cross Currency: Not supported in MVP (raises NotImplementedError)

        Args:
            symbol_spec: Static symbol specification
            current_price: Current market price (mid/bid/ask)

        Returns:
            tick_value for P&L calculations

        Raises:
            NotImplementedError: If cross-currency conversion needed

        Example:
            GBPUSD with Account=USD:
            → tick_value = 1.0 (Quote matches Account)

            GBPUSD with Account=GBP:
            → tick_value = 1.0 / 1.33000 = 0.7519

            GBPUSD with Account=JPY:
            → NotImplementedError (needs USDJPY rate)
        """
        # Quote Currency matches Account Currency
        # Example: GBPUSD with Account=USD
        if self.account_currency == symbol_spec.quote_currency:
            return 1.0  # No conversion needed

        # Base Currency matches Account Currency
        # Example: GBPUSD with Account=GBP
        elif self.account_currency == symbol_spec.base_currency:
            if current_price <= 0:
                raise ValueError(
                    f"Invalid price for tick_value calculation: {current_price}"
                )
            return 1.0 / current_price

        # Cross Currency - Not supported in MVP
        else:
            raise NotImplementedError(
                f"Cross-currency conversion not supported (MVP restriction): "
                f"Account Currency: {self.account_currency}, "
                f"Symbol: {symbol_spec.symbol} "
                f"(Base: {symbol_spec.base_currency}, Quote: {symbol_spec.quote_currency})\n"
                f"Supported configurations:\n"
                f"  - Account Currency == Quote Currency (e.g., GBPUSD with USD account)\n"
                f"  - Account Currency == Base Currency (e.g., GBPUSD with GBP account)\n"
                f"For cross-currency, external exchange rates would be required (Post-MVP)."
            )

    # ============================================
    # Order History
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

            vLog.info(f"Total orders: {len(history)}")
            vLog.info(f"Executed: {len(executed)}")
            vLog.info(f"Rejected: {len(rejected)}")
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
                vLog.info(f"Position {pos.position_id}:")
                vLog.info(f"  P&L: {pos.unrealized_pnl:.2f}")
                vLog.info(f"  Spread: {pos.get_spread_cost():.2f}")
                vLog.info(f"  Duration: {pos.close_time - pos.entry_time}")
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

    def get_symbol_spec(self, symbol: str) -> SymbolSpecification:
        """Get symbol specifications"""
        return self.broker.get_symbol_specification(symbol)

    # ============================================
    # Statistics ( & TYPED)
    # ============================================

    def get_execution_stats(self) -> ExecutionStats:
        """
        Get order execution statistics.

        Creates new ExecutionStats from direct attributes.
        Always returns new object (safe for external use).
        """
        return ExecutionStats(
            orders_sent=self._orders_sent,
            orders_executed=self._orders_executed,
            orders_rejected=self._orders_rejected,
            total_commission=self._total_commission,
            total_spread_cost=self._total_spread_cost
        )

    def reset(self) -> None:
        """Reset simulator to initial state"""
        self.portfolio.reset()
        self._current_prices.clear()
        self._order_counter = 0
        self._order_history.clear()  # EXTENDED

        # Reset direct attributes
        self._orders_sent = 0
        self._orders_executed = 0
        self._orders_rejected = 0
        self._total_commission = 0.0
        self._total_spread_cost = 0.0

    def get_tick_counter(self) -> int:
        return self._tick_counter
