# ============================================
# python/framework/trading_env/abstract_trade_executor.py
# ============================================
"""
FiniexTestingIDE - Abstract Trade Executor

Base class for all trade execution engines (TradeSimulator, LiveTradeExecutor).

This defines the contract that:
- process_tick_loop uses for tick processing
- DecisionTradingAPI routes orders through
- Framework code uses for statistics collection

Architecture:
    DecisionLogic → DecisionTradingAPI → AbstractTradeExecutor
                                              │
                                    ┌─────────┴──────────┐
                                    │                    │
                              TradeSimulator      LiveTradeExecutor
                              (Simulation)        (Horizon 2: Live)

Design Principles:
- DecisionLogic NEVER sees the executor directly (only DecisionTradingAPI)
- process_tick_loop calls on_tick() each tick (unified lifecycle)
- Portfolio is shared infrastructure (same PortfolioManager for all executors)
- Concrete methods: portfolio queries, broker info, fill processing
- Abstract methods: order submission, pending order handling (mode-specific)

Fill Processing:
- _fill_open_order() and _fill_close_order() are CONCRETE methods
- They handle portfolio updates, fee calculations, statistics — identical for all modes
- Subclasses call them when an order is confirmed (by latency sim or broker)
"""
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Optional, List, Dict, Tuple

from python.framework.factory.trading_fee_factory import (
    create_maker_taker_fee,
    create_spread_fee_from_tick
)
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trading_fee import AbstractTradingFee
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.portfolio_manager import PortfolioManager, Position
from python.framework.types.broker_types import FeeType, SymbolSpecification
from python.framework.types.latency_simulator_types import PendingOrder
from python.framework.types.market_data_types import TickData
from python.framework.types.order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    OrderResult,
    OrderCapabilities,
    RejectionReason,
    create_rejection_result,
)
from python.framework.types.pending_order_stats_types import PendingOrderStats
from python.framework.types.trading_env_stats_types import AccountInfo, ExecutionStats


class ExecutorMode(Enum):
    """Execution mode for trade executors."""
    SIMULATION = "simulation"
    # Horizon 2:
    # LIVE = "live"


class AbstractTradeExecutor(ABC):
    """
    Abstract base class for trade execution engines.

    Provides common infrastructure (portfolio, broker, statistics tracking)
    and concrete fill processing logic shared by all execution modes.

    Subclasses implement HOW orders are submitted (latency queue vs broker API).
    This base handles WHAT HAPPENS when an order is confirmed (fill processing).

    Subclasses:
        TradeSimulator: Simulated execution with latency modeling
        LiveTradeExecutor: Live execution via broker adapter (Horizon 2)
    """

    def __init__(
        self,
        broker_config: BrokerConfig,
        initial_balance: float,
        account_currency: str,
        logger: AbstractLogger,
        order_history_max: int = 10000,
        trade_history_max: int = 5000,
    ):
        self.broker = broker_config
        self.logger = logger
        self.account_currency = account_currency

        # Create portfolio manager with broker specifications
        broker_spec = self.broker.get_broker_specification()
        self.portfolio = PortfolioManager(
            logger=logger,
            initial_balance=initial_balance,
            account_currency=account_currency,
            broker_config=broker_config,
            leverage=broker_spec.leverage,
            margin_call_level=broker_spec.margin_call_level,
            stop_out_level=broker_spec.stopout_level,
            trade_history_max=trade_history_max
        )

        # Current market prices
        self._current_prices: Dict[str, Tuple[float, float]] = {}
        self._current_tick: Optional[TickData] = None
        self._tick_counter = 0

        # Order tracking with configurable limit
        self._order_counter = 0
        self._order_history_max = order_history_max
        self._order_history: deque[OrderResult] = deque(
            maxlen=order_history_max if order_history_max > 0 else None
        )
        self._order_history_limit_warned = False

        # Execution statistics
        self._orders_sent = 0
        self._orders_executed = 0
        self._orders_rejected = 0
        self._total_commission = 0.0
        self._total_spread_cost = 0.0

    def _check_order_history_limit(self) -> None:
        """Emit one-time warning when order history reaches capacity."""
        if (self._order_history_max > 0
                and not self._order_history_limit_warned
                and len(self._order_history) >= self._order_history_max):
            self._order_history_limit_warned = True
            self.logger.warning(
                f"⚠️ Order history limit reached ({self._order_history_max}). "
                f"Oldest entries will be discarded. Full history available in scenario log."
            )

    # ============================================
    # Tick Lifecycle (called by process_tick_loop)
    # ============================================

    def on_tick(self, tick: TickData) -> None:
        """
        Unified tick lifecycle — the ONLY method process_tick_loop calls.

        Handles price updates and mode-specific order processing in one call.
        Subclasses implement _process_pending_orders() for their specific logic.
        """
        self._current_tick = tick
        self._tick_counter += 1
        self._current_prices[tick.symbol] = (tick.bid, tick.ask)
        self.portfolio.mark_dirty(tick)
        self._process_pending_orders()

    @abstractmethod
    def _process_pending_orders(self) -> None:
        """
        Process pending orders (mode-specific, internal).

        TradeSimulator: Drains latency queue, calls _fill_open/close_order()
        LiveTradeExecutor: Polls broker for fills, calls _fill_open/close_order()
        """
        pass

    # ============================================
    # Order Submission (DecisionTradingAPI routes here)
    # ============================================

    @abstractmethod
    def open_order(
        self,
        symbol: str,
        order_type: OrderType,
        direction: OrderDirection,
        lots: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "",
        magic_number: int = 0,
    ) -> OrderResult:
        """
        Submit an order for execution.

        Args:
            symbol: Trading symbol
            order_type: MARKET or LIMIT
            direction: LONG or SHORT
            lots: Position size
            stop_loss: Optional stop loss price level
            take_profit: Optional take profit price level
            comment: Order comment
            magic_number: Strategy identifier

        TradeSimulator: Submits to latency queue → returns PENDING
        LiveTradeExecutor: Sends to broker API → returns PENDING/EXECUTED
        """
        pass

    @abstractmethod
    def close_position(
        self,
        position_id: str,
        lots: Optional[float] = None
    ) -> OrderResult:
        """
        Close a position (full or partial).

        TradeSimulator: Submits close to latency queue
        LiveTradeExecutor: Sends close to broker API
        """
        pass

    # ============================================
    # Fill Processing (concrete — shared by all modes)
    # ============================================

    def _fill_open_order(
        self,
        pending_order: PendingOrder,
        fill_price: Optional[float] = None
    ) -> None:
        """
        Process a confirmed OPEN order — update portfolio, fees, stats.

        Called by subclasses when their execution mechanism confirms a fill:
        - TradeSimulator: after latency delay completes (fill_price=None → use tick)
        - LiveTradeExecutor: after broker confirms fill (fill_price=broker's price)

        This is the SHARED fill logic — identical for simulation and live.
        Side-effect based: results are stored in _order_history (not returned).

        Args:
            pending_order: PendingOrder with order details
            fill_price: Broker-provided fill price. If None, determined from
                        current tick (ask for LONG, bid for SHORT).
        """
        self.logger.info(
            f"📋 Fill open order: {pending_order.pending_order_id} "
            f"at tick: {self._tick_counter}")
        self.logger.verbose(
            f"PENDING_ORDER_OPEN: {json.dumps(pending_order.to_dict(), indent=2)}")
        self.logger.verbose(
            f"CURRENT_TICK_AT_ORDER_OPEN: {json.dumps(self._current_tick.to_dict(), indent=2)}")

        # Get current price
        bid, ask = self.get_current_price(pending_order.symbol)

        # Determine entry price: broker-provided or derived from tick
        if fill_price is not None:
            entry_price = fill_price
        elif pending_order.direction == OrderDirection.LONG:
            entry_price = ask
        else:
            entry_price = bid

        # Get static symbol specification
        symbol_spec = self.broker.get_symbol_specification(pending_order.symbol)

        # Calculate tick_value dynamically
        tick_value = self._calculate_tick_value(symbol_spec, self._current_tick.mid)

        # Create entry fee based on broker fee model
        entry_fee = self._create_entry_fee(
            symbol_spec=symbol_spec,
            lots=pending_order.lots,
            entry_price=entry_price,
            tick_value=tick_value
        )

        self.logger.debug(
            f"💱 tick_value calculation: "
            f"Account={self.account_currency}, "
            f"Symbol={symbol_spec.symbol} "
            f"(Base={symbol_spec.base_currency}, Quote={symbol_spec.quote_currency}), "
            f"Price={self._current_tick.mid:.5f}, "
            f"tick_value={tick_value:.5f}"
        )

        # Check margin available (only if leverage trading enabled)
        leverage = self.broker.get_max_leverage()
        if leverage > 1:
            margin_required = self.broker.calculate_margin(
                pending_order.symbol, pending_order.lots,
                self._current_tick, pending_order.direction)
            free_margin = self.portfolio.get_free_margin(pending_order.direction)

            if margin_required > free_margin:
                self._orders_rejected += 1
                rejection = create_rejection_result(
                    order_id=pending_order.pending_order_id,
                    reason=RejectionReason.INSUFFICIENT_MARGIN,
                    message=f"Required margin {margin_required:.2f} exceeds free margin {free_margin:.2f}"
                )
                self._check_order_history_limit()
                self._order_history.append(rejection)
                self.logger.warning(
                    f"Order {pending_order.pending_order_id} rejected: "
                    f"margin {margin_required:.2f} > free {free_margin:.2f}"
                )
                return

        # Open position in portfolio
        position = self.portfolio.open_position(
            order_id=pending_order.pending_order_id,
            symbol=pending_order.symbol,
            direction=pending_order.direction,
            lots=pending_order.lots,
            entry_price=entry_price,
            entry_tick_value=tick_value,
            entry_bid=bid,
            entry_ask=ask,
            digits=symbol_spec.digits,
            contract_size=symbol_spec.contract_size,
            entry_tick_index=self._tick_counter,
            entry_fee=entry_fee,
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
            execution_time=datetime.now(timezone.utc),
            commission=0.0,
            broker_order_id=position.position_id,
            metadata={
                "symbol": pending_order.symbol,
                "direction": pending_order.direction,
                "position_id": position.position_id,
                "fee_cost": entry_fee.cost,
                "fee_type": entry_fee.fee_type.value,
                "filled_at_tick": self._tick_counter
            }
        )

        self.logger.debug(f"Fill open order finished")
        self.logger.verbose(
            f"OPEN_ORDER_RESULT: {json.dumps(result.to_dict(), indent=2)}")

        # Update statistics
        self._orders_executed += 1
        self._total_spread_cost += entry_fee.cost
        self._check_order_history_limit()
        self._order_history.append(result)

    def _fill_close_order(
        self,
        pending_order: PendingOrder,
        fill_price: Optional[float] = None
    ) -> None:
        """
        Process a confirmed CLOSE order — update portfolio, record PnL.

        Called by subclasses when their execution mechanism confirms a close:
        - TradeSimulator: after latency delay completes (fill_price=None → use tick)
        - LiveTradeExecutor: after broker confirms close (fill_price=broker's price)

        This is the SHARED fill logic — identical for simulation and live.

        Args:
            pending_order: PendingOrder with close details
            fill_price: Broker-provided close price. If None, determined from
                        current tick (bid for LONG close, ask for SHORT close).
        """
        self.logger.info(
            f"📋 Fill close order {pending_order.pending_order_id}, "
            f"at tick: {self._tick_counter}")
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

        # Get current price (needed for tick_value calculation regardless)
        bid, ask = self.get_current_price(position.symbol)

        # Determine close price: broker-provided or derived from tick
        if fill_price is not None:
            close_price = fill_price
        elif position.direction == OrderDirection.LONG:
            close_price = bid  # Sell at bid
        else:
            close_price = ask  # Buy back at ask

        # Calculate exit tick_value for trade record
        symbol_spec = self.broker.get_symbol_specification(position.symbol)
        exit_tick_value = self._calculate_tick_value(
            symbol_spec, (bid + ask) / 2.0)

        # Close position with exit tick_value
        realized_pnl = self.portfolio.close_position_portfolio(
            position_id=pending_order.pending_order_id,
            exit_price=close_price,
            exit_tick_value=exit_tick_value,
            exit_tick_index=self._tick_counter,
            exit_fee=None  # MVP: No exit commission
        )

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
            execution_time=datetime.now(timezone.utc),
            commission=0.0,
            metadata={
                "realized_pnl": realized_pnl,
                "position_id": pending_order.pending_order_id
            }
        )

        self.logger.debug(f"Fill close order finished")
        self.logger.verbose(
            f"CLOSE_FILL_ORDER: {json.dumps(result.to_dict(), indent=2)}")

        self._check_order_history_limit()
        self._order_history.append(result)

    # ============================================
    # Position Management
    # ============================================

    def modify_position(
        self,
        position_id: str,
        new_stop_loss: Optional[float] = None,
        new_take_profit: Optional[float] = None
    ) -> bool:
        """Modify position SL/TP. Delegates to portfolio."""
        return self.portfolio.modify_position(
            position_id=position_id,
            new_stop_loss=new_stop_loss,
            new_take_profit=new_take_profit
        )

    # ============================================
    # Queries (concrete - same for all executors)
    # ============================================

    def get_account_info(self, order_direction: OrderDirection) -> AccountInfo:
        """Get current account information. Returns copy (safe for external use)."""
        return self.portfolio.get_account_info(order_direction)

    def get_open_positions(self) -> List[Position]:
        """
        Get all confirmed open positions from portfolio.

        Returns ONLY positions that are actually open (filled).
        No pseudo-positions, no pending-order mixing.
        Decision Logic uses has_pending_orders() / is_pending_close()
        separately for order-in-flight awareness.
        """
        return self.portfolio.get_open_positions()

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get specific position by ID."""
        return self.portfolio.get_position(position_id)

    # ============================================
    # Pending Order Awareness (for Decision Logic via API)
    # ============================================

    @abstractmethod
    def has_pending_orders(self) -> bool:
        """
        Are there any orders in flight (submitted but not yet filled)?

        Used by single-position strategies to avoid double-ordering.

        TradeSimulator: Checks latency queue for pending opens/closes
        LiveTradeExecutor: Checks broker order status cache
        """
        pass

    @abstractmethod
    def is_pending_close(self, position_id: str) -> bool:
        """
        Is this specific position currently being closed (close order in flight)?

        Used by multi-position strategies to avoid duplicate close submissions.

        TradeSimulator: Checks latency queue for pending close on this position
        LiveTradeExecutor: Checks broker close order status
        """
        pass

    def get_balance(self) -> float:
        """Get account balance."""
        return self.portfolio.balance

    def get_current_price(self, symbol: str) -> Tuple[float, float]:
        """Get current bid/ask price for symbol."""
        if not self._current_tick or self._current_tick.symbol != symbol:
            raise ValueError(f"No current price data for {symbol}")
        return self._current_tick.bid, self._current_tick.ask

    # ============================================
    # Post-Run (Framework statistics collection)
    # ============================================

    @abstractmethod
    def close_all_remaining_orders(self, current_tick: int = 0) -> None:
        """
        Close all remaining open positions at end of run.

        TradeSimulator: Force-flushes latency queue, closes all
        LiveTradeExecutor: Sends close orders to broker for all open positions

        Args:
            current_tick: Current tick number for pending latency calculation
        """
        pass

    def check_clean_shutdown(self) -> bool:
        """
        Post-cleanup safety check — call after close_all_remaining_orders().

        Logs errors for any orphaned positions or pending orders that
        survived cleanup. Does NOT raise — reports are still generated.

        Returns:
            True if shutdown was clean, False if orphaned state detected.
        """
        clean = True

        open_positions = self.get_open_positions()
        if open_positions:
            clean = False
            for pos in open_positions:
                self.logger.error(
                    f"Orphaned position after cleanup: {pos.position_id} "
                    f"{pos.direction.value} {pos.lots} lots {pos.symbol}"
                )

        if self.has_pending_orders():
            clean = False
            self.logger.error(
                "Orphaned pending orders after cleanup — orders still in pipeline"
            )

        return clean

    @abstractmethod
    def get_pending_stats(self) -> PendingOrderStats:
        """
        Get aggregated pending order statistics (latency, outcomes).

        Returns:
            PendingOrderStats with latency metrics and anomaly records
        """
        pass

    def get_execution_stats(self) -> ExecutionStats:
        """Get order execution statistics."""
        return ExecutionStats(
            orders_sent=self._orders_sent,
            orders_executed=self._orders_executed,
            orders_rejected=self._orders_rejected,
            total_commission=self._total_commission,
            total_spread_cost=self._total_spread_cost
        )

    def get_order_history(self) -> List[OrderResult]:
        """Get complete order history."""
        return list(self._order_history)

    def get_trade_history(self) -> List:
        """Get all completed trades with full audit trail."""
        return self.portfolio.get_trade_history()

    # ============================================
    # Broker Information
    # ============================================

    def get_broker_name(self) -> str:
        """Get broker name."""
        return self.broker.get_broker_name()

    def get_broker_capabilities(self) -> OrderCapabilities:
        """Get broker order capabilities."""
        return self.broker.get_order_capabilities()

    def get_symbol_spec(self, symbol: str) -> SymbolSpecification:
        """Get symbol specifications."""
        return self.broker.get_symbol_specification(symbol)

    # ============================================
    # Lifecycle
    # ============================================

    def get_tick_counter(self) -> int:
        """Get current tick counter."""
        return self._tick_counter

    def reset(self) -> None:
        """Reset executor to initial state."""
        self.portfolio.reset()
        self._current_prices.clear()
        self._order_counter = 0
        self._order_history.clear()
        self._orders_sent = 0
        self._orders_executed = 0
        self._orders_rejected = 0
        self._total_commission = 0.0
        self._total_spread_cost = 0.0

    # ============================================
    # Shared Calculations (used by subclasses)
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
        """
        if self.account_currency == symbol_spec.quote_currency:
            return 1.0

        elif self.account_currency == symbol_spec.base_currency:
            if current_price <= 0:
                raise ValueError(
                    f"Invalid price for tick_value calculation: {current_price}"
                )
            return 1.0 / current_price

        else:
            raise NotImplementedError(
                f"Cross-currency conversion not supported (MVP restriction): "
                f"Account Currency: '{self.account_currency}', "
                f"Symbol: {symbol_spec.symbol} "
                f"(Base: {symbol_spec.base_currency}, Quote: {symbol_spec.quote_currency})\n"
                f"Supported configurations:\n"
                f"  - Account Currency == Quote Currency (e.g., GBPUSD with USD account)\n"
                f"  - Account Currency == Base Currency (e.g., GBPUSD with GBP account)\n"
                f"For cross-currency, external exchange rates would be required (Post-MVP)."
            )

    def _create_entry_fee(
        self,
        symbol_spec: SymbolSpecification,
        lots: float,
        entry_price: float,
        tick_value: float
    ) -> AbstractTradingFee:
        """
        Create entry fee based on broker fee model.

        Determines fee type from broker config and creates appropriate fee object.
        Spread-based (MT5) vs Maker/Taker (Kraken).
        """
        fee_model_str = self.broker.adapter.broker_config.get(
            'fee_structure', {}
        ).get('model', 'spread')

        fee_model = FeeType(fee_model_str)

        if fee_model == FeeType.MAKER_TAKER:
            return create_maker_taker_fee(
                lots=lots,
                contract_size=symbol_spec.contract_size,
                entry_price=entry_price,
                maker_rate=self.broker.adapter.get_maker_fee(),
                taker_rate=self.broker.adapter.get_taker_fee(),
                is_maker=False  # Market order = Taker
            )

        # Default: Spread-based fee (MT5, Forex)
        return create_spread_fee_from_tick(
            tick=self._current_tick,
            lots=lots,
            tick_value=tick_value,
            digits=symbol_spec.digits
        )
