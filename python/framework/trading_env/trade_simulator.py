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
from typing import Optional, List, Dict, Union

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.order_latency_simulator import OrderLatencySimulator
from python.framework.types.latency_simulator_types import PendingOrder, PendingOrderAction, PendingOrderOutcome
from python.framework.types.portfolio_trade_record_types import CloseReason, EntryType
from python.framework.types.pending_order_stats_types import PendingOrderStats
from .broker_config import BrokerConfig
from python.framework.trading_env.portfolio_manager import UNSET, _UnsetType
from ..types.order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    OrderResult,
    RejectionReason,
    FillType,
    ModificationRejectionReason,
    ModificationResult,
    OpenOrderRequest,
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

        # Active limit orders waiting for price trigger (post-latency)
        self._active_limit_orders: List[PendingOrder] = []

    # ============================================
    # Pending Order Processing (simulation-specific)
    # ============================================

    def _process_pending_orders(self) -> None:
        """
        Two-phase pending order processing.

        Phase 1: Drain latency queue (broker accepted orders after delay).
          - MARKET OPEN → fill immediately at current tick price
          - LIMIT OPEN → check if price already reached → fill LIMIT_IMMEDIATE,
            else move to _active_limit_orders for price monitoring
          - CLOSE → fill immediately

        Phase 2: Check active limit orders for price trigger.
          - LONG limit: ask <= limit_price → fill at limit_price
          - SHORT limit: bid >= limit_price → fill at limit_price
        """
        # === Phase 1: Latency queue drain ===
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

                    # Limit orders: check immediate fill or queue for price monitoring
                    if pending_order.order_type == OrderType.LIMIT:
                        if self._is_limit_price_reached(pending_order):
                            # Price already past limit → fill immediately
                            self._fill_open_order(
                                pending_order,
                                fill_price=pending_order.entry_price,
                                entry_type=EntryType.LIMIT,
                                fill_type=FillType.LIMIT_IMMEDIATE
                            )
                            self.logger.info(
                                f"⚡ Limit order {pending_order.pending_order_id} "
                                f"filled immediately at {pending_order.entry_price:.5f} "
                                f"(price already reached after latency)")
                        else:
                            # Price not reached → queue for per-tick monitoring
                            self._active_limit_orders.append(pending_order)
                            self.logger.info(
                                f"📋 Limit order {pending_order.pending_order_id} "
                                f"activated — waiting for price {pending_order.entry_price:.5f}")
                    else:
                        # Market order → fill at current tick price
                        self._fill_open_order(pending_order)

                    self.latency_simulator.record_outcome(
                        pending_order, PendingOrderOutcome.FILLED,
                        latency_ticks=latency_ticks)
                case PendingOrderAction.CLOSE:
                    self._fill_close_order(pending_order)
                    self.latency_simulator.record_outcome(
                        pending_order, PendingOrderOutcome.FILLED,
                        latency_ticks=latency_ticks)

        # === Phase 2: Active limit order price monitoring ===
        if self._active_limit_orders and self._current_tick:
            remaining: List[PendingOrder] = []
            for pending in self._active_limit_orders:
                if pending.symbol != self._current_tick.symbol:
                    remaining.append(pending)
                    continue

                if self._is_limit_price_reached(pending):
                    self._fill_open_order(
                        pending,
                        fill_price=pending.entry_price,
                        entry_type=EntryType.LIMIT,
                        fill_type=FillType.LIMIT
                    )
                    self.logger.info(
                        f"🎯 Limit order {pending.pending_order_id} triggered "
                        f"at {pending.entry_price:.5f} "
                        f"(bid={self._current_tick.bid:.5f}, ask={self._current_tick.ask:.5f})")
                else:
                    remaining.append(pending)
            self._active_limit_orders = remaining

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

    def open_order(self, request: OpenOrderRequest) -> OrderResult:
        """
        Send order to broker simulation.

        Validates order parameters, then submits to latency simulator.
        Order will be filled after deterministic delay via _process_pending_orders().

        Args:
            request: OpenOrderRequest with all order parameters

        Returns:
            OrderResult with PENDING status (or rejection)
        """
        self._orders_sent += 1

        # Generate order ID
        self._order_counter += 1
        order_id = self.portfolio.get_next_position_id(request.symbol)

        # Pre-delay validation (doesn't need to wait for latency)

        # Validate order
        is_valid, error = self.broker.validate_order(request.symbol, request.lots)
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
        if not self.broker.is_symbol_tradeable(request.symbol):
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.SYMBOL_NOT_TRADEABLE,
                message=f"Symbol {request.symbol} not tradeable"
            )
            self._order_history.append(result)
            return result

        # Execute based on order type
        if request.order_type == OrderType.MARKET:
            # Submit to latency simulator (fill happens later)
            self.latency_simulator.submit_open_order(
                order_id=order_id,
                request=request,
                current_tick=self._tick_counter,
            )
            # Return PENDING status
            result = OrderResult(
                order_id=order_id,
                status=OrderStatus.PENDING,
                metadata={
                    "symbol": request.symbol,
                    "direction": request.direction,
                    "lots": request.lots,
                    "submitted_at_tick": self._tick_counter
                }
            )
        elif request.order_type == OrderType.LIMIT:
            # Validate limit price
            if request.price is None or request.price <= 0:
                self._orders_rejected += 1
                result = create_rejection_result(
                    order_id=order_id,
                    reason=RejectionReason.INVALID_PRICE,
                    message=f"Limit order requires positive price, got: {request.price}"
                )
                self._order_history.append(result)
                return result

            # Submit to latency simulator with limit price
            self.latency_simulator.submit_open_order(
                order_id=order_id,
                request=request,
                current_tick=self._tick_counter,
            )
            # Return PENDING status
            result = OrderResult(
                order_id=order_id,
                status=OrderStatus.PENDING,
                metadata={
                    "symbol": request.symbol,
                    "direction": request.direction,
                    "lots": request.lots,
                    "limit_price": request.price,
                    "submitted_at_tick": self._tick_counter
                }
            )
        else:
            # Extended orders — not supported
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
                message=f"Order type {request.order_type} not supported"
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
    # Limit Order Helpers
    # ============================================

    def _is_limit_price_reached(self, pending: PendingOrder) -> bool:
        """
        Check if current tick price has reached the limit price.

        Args:
            pending: Limit order with entry_price = limit price

        Returns:
            True if limit price is reached (order should fill)
        """
        if not self._current_tick or pending.symbol != self._current_tick.symbol:
            return False

        if pending.direction == OrderDirection.LONG:
            # Buy limit: fill when ask <= limit price
            return self._current_tick.ask <= pending.entry_price
        else:
            # Sell limit: fill when bid >= limit price
            return self._current_tick.bid >= pending.entry_price

    def get_active_limit_order_count(self) -> int:
        """Get number of active limit orders waiting for price trigger."""
        return len(self._active_limit_orders)

    def cancel_limit_order(self, order_id: str) -> bool:
        """
        Cancel an active limit order by order ID.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if order was found and cancelled
        """
        for i, pending in enumerate(self._active_limit_orders):
            if pending.pending_order_id == order_id:
                self._active_limit_orders.pop(i)
                self.logger.info(f"❌ Limit order {order_id} cancelled")
                return True
        return False

    def modify_limit_order(
        self,
        order_id: str,
        new_price: Union[float, _UnsetType] = UNSET,
        new_stop_loss: Union[float, None, _UnsetType] = UNSET,
        new_take_profit: Union[float, None, _UnsetType] = UNSET
    ) -> ModificationResult:
        """
        Modify a pending limit order's price, SL, and/or TP.

        Searches _active_limit_orders (post-latency, waiting for price trigger).
        Validates SL/TP against the effective limit price, not current tick.

        Args:
            order_id: Pending limit order ID
            new_price: New limit price (UNSET=keep current)
            new_stop_loss: New SL level (UNSET=no change, None=remove)
            new_take_profit: New TP level (UNSET=no change, None=remove)

        Returns:
            ModificationResult with success status and rejection reason
        """
        # Find pending limit order
        pending = None
        for p in self._active_limit_orders:
            if p.pending_order_id == order_id:
                pending = p
                break

        if pending is None:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.LIMIT_ORDER_NOT_FOUND)

        # Validate new price
        if not isinstance(new_price, _UnsetType):
            if new_price <= 0:
                return ModificationResult(
                    success=False,
                    rejection_reason=ModificationRejectionReason.INVALID_PRICE)

        # Determine effective values (merge UNSET with current)
        effective_price = pending.entry_price if isinstance(new_price, _UnsetType) else new_price

        current_sl = pending.order_kwargs.get('stop_loss') if pending.order_kwargs else None
        current_tp = pending.order_kwargs.get('take_profit') if pending.order_kwargs else None
        effective_sl = current_sl if isinstance(new_stop_loss, _UnsetType) else new_stop_loss
        effective_tp = current_tp if isinstance(new_take_profit, _UnsetType) else new_take_profit

        # Validate SL/TP against limit price (not current tick)
        rejection = self._validate_limit_order_sl_tp(
            pending.direction, effective_price, effective_sl, effective_tp)
        if rejection is not None:
            return ModificationResult(success=False, rejection_reason=rejection)

        # Apply changes
        if not isinstance(new_price, _UnsetType):
            pending.entry_price = new_price
        if not isinstance(new_stop_loss, _UnsetType):
            if pending.order_kwargs is None:
                pending.order_kwargs = {}
            if new_stop_loss is None:
                pending.order_kwargs.pop('stop_loss', None)
            else:
                pending.order_kwargs['stop_loss'] = new_stop_loss
        if not isinstance(new_take_profit, _UnsetType):
            if pending.order_kwargs is None:
                pending.order_kwargs = {}
            if new_take_profit is None:
                pending.order_kwargs.pop('take_profit', None)
            else:
                pending.order_kwargs['take_profit'] = new_take_profit

        self.logger.info(
            f"✏️ Limit order {order_id} modified — "
            f"price={effective_price:.5f}, sl={effective_sl}, tp={effective_tp}")

        return ModificationResult(success=True)

    def _validate_limit_order_sl_tp(
        self,
        direction: OrderDirection,
        limit_price: float,
        stop_loss: Optional[float],
        take_profit: Optional[float]
    ) -> Optional[ModificationRejectionReason]:
        """
        Validate SL/TP against limit price (not current tick).

        Position will open at limit_price, so SL/TP must be valid relative to it.

        Args:
            direction: Order direction (LONG/SHORT)
            limit_price: The limit entry price
            stop_loss: Stop loss level (None = no SL)
            take_profit: Take profit level (None = no TP)

        Returns:
            None if valid, ModificationRejectionReason if invalid
        """
        if stop_loss is not None:
            if direction == OrderDirection.LONG and stop_loss >= limit_price:
                self.logger.warning(
                    f"Invalid SL for LONG limit: sl={stop_loss} >= price={limit_price}")
                return ModificationRejectionReason.INVALID_SL_LEVEL
            if direction == OrderDirection.SHORT and stop_loss <= limit_price:
                self.logger.warning(
                    f"Invalid SL for SHORT limit: sl={stop_loss} <= price={limit_price}")
                return ModificationRejectionReason.INVALID_SL_LEVEL

        if take_profit is not None:
            if direction == OrderDirection.LONG and take_profit <= limit_price:
                self.logger.warning(
                    f"Invalid TP for LONG limit: tp={take_profit} <= price={limit_price}")
                return ModificationRejectionReason.INVALID_TP_LEVEL
            if direction == OrderDirection.SHORT and take_profit >= limit_price:
                self.logger.warning(
                    f"Invalid TP for SHORT limit: tp={take_profit} >= price={limit_price}")
                return ModificationRejectionReason.INVALID_TP_LEVEL

        # SL and TP must not cross each other
        if stop_loss is not None and take_profit is not None:
            if direction == OrderDirection.LONG and stop_loss >= take_profit:
                self.logger.warning(
                    f"SL/TP cross for LONG limit: sl={stop_loss} >= tp={take_profit}")
                return ModificationRejectionReason.SL_TP_CROSS
            if direction == OrderDirection.SHORT and stop_loss <= take_profit:
                self.logger.warning(
                    f"SL/TP cross for SHORT limit: sl={stop_loss} <= tp={take_profit}")
                return ModificationRejectionReason.SL_TP_CROSS

        return None

    # ============================================
    # Pending Order Awareness
    # ============================================

    def has_pending_orders(self) -> bool:
        """Check if any orders are in the latency queue or active limit orders."""
        return (self.latency_simulator.has_pending_orders()
                or len(self._active_limit_orders) > 0)

    def is_pending_close(self, position_id: str) -> bool:
        """Check if a specific position has a pending close order."""
        return self.latency_simulator.is_pending_close(position_id)

    def get_pending_stats(self) -> PendingOrderStats:
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
                self._fill_close_order(synthetic, close_reason=CloseReason.SCENARIO_END)

        # Clear active limit orders (unfilled limits at scenario end)
        if self._active_limit_orders:
            self.logger.warning(
                f"{len(self._active_limit_orders)} unfilled limit orders "
                f"at scenario end — discarding"
            )
            self._active_limit_orders.clear()

        # Catch genuine stuck-in-pipeline orders (real anomalies)
        self.latency_simulator.clear_pending(
            current_tick=current_tick, reason="scenario_end")
