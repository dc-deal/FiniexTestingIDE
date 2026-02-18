# ============================================
# python/framework/trading_env/trade_simulator.py
# ============================================
"""
FiniexTestingIDE - Trade Simulator
Simulates broker trading environment with realistic execution

Inherits from AbstractTradeExecutor - provides simulated order execution
with deterministic latency modeling via OrderLatencySimulator.

Key Simulation Features:
- Order latency: Seeded delays between order submission and fill
- Pending order lifecycle: PENDING → EXECUTED with realistic timing
- Fill processing: Inherited from AbstractTradeExecutor (shared with live)

CHANGES:
- Direct attributes for execution stats (_orders_sent, _orders_executed, etc.)
- Always-copy public API (using replace())
- Cleaner, more maintainable code structure
- FULLY TYPED: All statistics methods return dataclasses (no more dicts!)
- CURRENCY: account_currency with auto-detection from symbol
- REFACTOR: Inherits from AbstractTradeExecutor for live trading foundation
- REFACTOR: Fill logic (_fill_open_order, _fill_close_order) moved to base class
- REFACTOR: Pseudo-positions eliminated — Decision Logic uses has_pending_orders()
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.order_latency_simulator import OrderLatencySimulator
from python.framework.types.latency_simulator_types import PendingOrderAction, PendingOrderOutcome
from .broker_config import BrokerConfig
from ..types.order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    OrderResult,
    RejectionReason,
    create_rejection_result
)


# ============================================
# Stress Test Configuration (Workaround — will be config-driven later)
# ============================================
STRESS_TEST_REJECTION_ENABLED = False
STRESS_TEST_REJECT_EVERY_N = 3


class TradeSimulator(AbstractTradeExecutor):
    """
    Trade Simulator - Simulated order execution with latency modeling.

    Extends AbstractTradeExecutor with:
    - OrderLatencySimulator for deterministic execution delays
    - Pending order lifecycle management (submit → latency delay → fill)

    Fill processing (_fill_open_order, _fill_close_order) is inherited
    from the base class — identical logic for simulation and live trading.

    CURRENCY HANDLING:
    - Supports "auto" detection: account_currency = symbol quote currency
    - Logs currency operations for transparency
    """

    def __init__(
        self,
        broker_config: BrokerConfig,
        initial_balance: float,
        account_currency: str,
        logger: AbstractLogger,
        seeds: Optional[Dict[str, int]] = None,
        order_history_max: int = 10000,
        trade_history_max: int = 5000,
    ):
        """
        Initialize trade simulator.

        Args:
            broker_config: Broker configuration with spreads and capabilities
            initial_balance: Starting account balance
            account_currency: Account currency (or "auto" for symbol-based detection)
            logger: Logger instance
            seeds: Seeds for order execution delays (from config)
            order_history_max: Max order history entries (0=unlimited)
            trade_history_max: Max trade history entries (0=unlimited)
        """
        # Initialize common infrastructure (portfolio, broker, counters, fill logic)
        super().__init__(
            broker_config=broker_config,
            initial_balance=initial_balance,
            account_currency=account_currency,
            logger=logger,
            order_history_max=order_history_max,
            trade_history_max=trade_history_max
        )

        # Order latency simulator with deterministic delays
        seeds = seeds or {}
        self.latency_simulator = OrderLatencySimulator(
            seeds, logger
        )

    # ============================================
    # Pending Order Processing (simulation-specific)
    # ============================================

    def _process_pending_orders(self) -> None:
        """
        Process orders that have completed their latency delay.

        Drains the latency queue and calls inherited fill methods
        from AbstractTradeExecutor for portfolio updates.
        Records pending order outcomes for latency statistics.
        """
        filled_orders = self.latency_simulator.process_tick(self._tick_counter)

        for pending_order in filled_orders:
            # Latency = fill_at_tick - placed_at_tick (planned delay)
            latency_ticks = None
            if pending_order.fill_at_tick is not None and pending_order.placed_at_tick is not None:
                latency_ticks = pending_order.fill_at_tick - pending_order.placed_at_tick

            match pending_order.order_action:
                case PendingOrderAction.OPEN:
                    if self._stress_test_should_reject(pending_order):
                        self.latency_simulator.record_outcome(
                            pending_order, PendingOrderOutcome.REJECTED,
                            latency_ticks=latency_ticks)
                        continue
                    self._fill_open_order(pending_order)
                    self.latency_simulator.record_outcome(
                        pending_order, PendingOrderOutcome.FILLED,
                        latency_ticks=latency_ticks)
                case PendingOrderAction.CLOSE:
                    self._fill_close_order(pending_order)
                    self.latency_simulator.record_outcome(
                        pending_order, PendingOrderOutcome.FILLED,
                        latency_ticks=latency_ticks)

    # ============================================
    # Stress Test: Seeded Rejection (toggle via module constants)
    # ============================================

    def _stress_test_should_reject(self, pending_order) -> bool:
        """
        Check if this order should be rejected by the stress test.

        Controlled by STRESS_TEST_REJECTION_ENABLED and STRESS_TEST_REJECT_EVERY_N
        module-level constants. Returns True if order was rejected (and handled).
        """
        if not STRESS_TEST_REJECTION_ENABLED:
            return False

        self.latency_simulator._fill_counter += 1
        if self.latency_simulator._fill_counter % STRESS_TEST_REJECT_EVERY_N != 0:
            return False

        rejection = create_rejection_result(
            order_id=pending_order.pending_order_id,
            reason=RejectionReason.BROKER_ERROR,
            message=f"[STRESS TEST] Seeded rejection for order #{self.latency_simulator._fill_counter}"
        )
        self._orders_rejected += 1
        self._order_history.append(rejection)
        self.logger.warning(
            f"[STRESS TEST] Order {pending_order.pending_order_id} rejected "
            f"(every {STRESS_TEST_REJECT_EVERY_N}. order rule)"
        )
        return True

    # ============================================
    # Order Submission (simulation-specific)
    # ============================================

    def open_order(
        self,
        symbol: str,
        order_type: OrderType,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> OrderResult:
        """
        Send order to broker simulation.

        Validates order parameters, then submits to latency simulator.
        Order will be filled after deterministic delay via _process_pending_orders().
        """
        self._orders_sent += 1

        # Generate order ID
        self._order_counter += 1
        order_id = self.portfolio.get_next_position_id(symbol)

        # Pre-delay validation (doesn't need to wait for latency)

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
            # Submit to latency simulator (fill happens later)
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

    # ============================================
    # Close Commands (simulation-specific)
    # ============================================

    def close_position(
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
            execution_time=datetime.now(timezone.utc),
            commission=0.0,
            metadata={
                "position_id": position_id,
                "awaiting_fill": True
            }
        )

    # ============================================
    # Pending Order Awareness
    # ============================================

    def has_pending_orders(self) -> bool:
        """Check if any orders are in the latency queue."""
        return self.latency_simulator.has_pending_orders()

    def is_pending_close(self, position_id: str) -> bool:
        """Check if a specific position has a pending close order."""
        return self.latency_simulator.is_pending_close(position_id)

    def get_pending_stats(self) -> 'PendingOrderStats':
        """
        Get aggregated pending order statistics from latency simulator.

        Returns:
            PendingOrderStats with tick-based latency metrics
        """
        return self.latency_simulator.get_pending_stats()

    # ============================================
    # Cleanup
    # ============================================

    def close_all_remaining_orders(self, current_tick: int = 0) -> None:
        """
        BEFORE collecting statistics — cleanup at scenario end.

        Two-phase cleanup:
        1. Direct-fill open positions using synthetic PendingOrders.
           These bypass the latency pipeline entirely — no pending created,
           no FORCE_CLOSED in statistics. This is an internal cleanup,
           not an algo-initiated action.
        2. clear_pending() catches genuine stuck-in-pipeline orders
           (e.g. algo submitted an order right before scenario ended,
           still waiting for latency delay). These ARE real anomalies
           and correctly recorded as FORCE_CLOSED with reason="scenario_end".

        Args:
            current_tick: Current tick number for latency calculation
        """
        open_positions = self.get_open_positions()
        if open_positions:
            self.logger.warning(
                f"{len(open_positions)} positions remain open — direct-closing (no pending)"
            )
            # Direct fill via synthetic PendingOrder — bypasses latency pipeline
            for pos in open_positions:
                synthetic = self.latency_simulator.create_synthetic_close_order(
                    pos.position_id)
                self._fill_close_order(synthetic)

        # Catch genuine stuck-in-pipeline orders (real anomalies)
        self.latency_simulator.clear_pending(
            current_tick=current_tick, reason="scenario_end")
