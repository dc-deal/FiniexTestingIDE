# ============================================
# python/framework/trading_env/live/live_trade_executor.py
# ============================================
"""
FiniexTestingIDE - Live Trade Executor
Live broker execution via adapter API (Horizon 2).

Inherits from AbstractTradeExecutor — provides live order execution
with broker adapter communication and LiveOrderTracker for pending
order management.

Architecture:
    FiniexAutoTrader (live runner, replaces process_tick_loop)
        |
        +-- Live Data Feed (WebSocket / REST poll) -> delivers ticks
        |
        +-- LiveTradeExecutor
            +-- on_tick(tick)                -> inherited: prices + _process_pending_orders()
            +-- _process_pending_orders()    -> polls broker for fills, handles timeouts
            +-- open_order()                 -> sends to broker via adapter
            +-- close_position()             -> sends close to broker
            +-- _fill_open_order()           -> INHERITED: portfolio update
            +-- _fill_close_order()          -> INHERITED: portfolio update

Fill processing is INHERITED from AbstractTradeExecutor — no duplication needed.
The base class handles: portfolio updates, fee calculations, statistics, PnL.

Feature gating: MARKET + LIMIT orders supported. Limit order modification is broker-side (future).
"""

from datetime import datetime, timezone
from typing import Dict, Optional, Union

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor, ExecutorMode
from python.framework.trading_env.portfolio_manager import UNSET, _UnsetType
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_order_tracker import LiveOrderTracker
from python.framework.types.latency_simulator_types import PendingOrder, PendingOrderAction, PendingOrderOutcome
from python.framework.types.portfolio_trade_record_types import CloseReason
from python.framework.types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
    TimeoutConfig,
)
from python.framework.types.order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    OrderResult,
    RejectionReason,
    ModificationRejectionReason,
    ModificationResult,
    OpenOrderRequest,
    create_rejection_result,
)
from python.framework.types.pending_order_stats_types import PendingOrderStats


class LiveTradeExecutor(AbstractTradeExecutor):
    """
    Live Trade Executor — broker execution via adapter API.

    Extends AbstractTradeExecutor with live-specific behavior:
    - Order submission via adapter.execute_order()
    - Pending order tracking via LiveOrderTracker
    - Broker status polling in _process_pending_orders()
    - Timeout detection for unresponsive orders

    The AbstractTradeExecutor base provides:
    - Portfolio management (positions, balance, margin)
    - Fill processing (_fill_open_order, _fill_close_order)
    - Fee calculations, statistics, order history
    - Price tracking, broker info queries

    This subclass implements:
    - HOW orders are submitted (broker API)
    - HOW fills are detected (broker polling)
    - HOW pending state is tracked (LiveOrderTracker)
    """

    def __init__(
        self,
        broker_config: BrokerConfig,
        initial_balance: float,
        account_currency: str,
        logger: AbstractLogger,
        timeout_config: Optional[TimeoutConfig] = None,
        order_history_max: int = 10000,
        trade_history_max: int = 5000,
    ):
        """
        Initialize live trade executor.

        Args:
            broker_config: Broker configuration (must have live-capable adapter)
            initial_balance: Starting account balance
            account_currency: Account currency
            logger: Logger instance
            timeout_config: Timeout thresholds for order monitoring
            order_history_max: Max order history entries (0=unlimited)
            trade_history_max: Max trade history entries (0=unlimited)
        """
        super().__init__(
            broker_config=broker_config,
            initial_balance=initial_balance,
            account_currency=account_currency,
            logger=logger,
            order_history_max=order_history_max,
            trade_history_max=trade_history_max,
        )

        # Validate adapter supports live execution
        if not broker_config.adapter.is_live_capable():
            raise ValueError(
                f"Adapter '{broker_config.get_broker_name()}' is not live-capable. "
                f"Live execution requires adapter.is_live_capable() == True."
            )

        self._timeout_config = timeout_config or TimeoutConfig()

        # Live order tracker with broker ref tracking
        self._order_tracker = LiveOrderTracker(
            logger=logger,
            timeout_config=self._timeout_config,
        )

        # Live mode: broker handles SL/TP server-side
        self._executor_mode = ExecutorMode.LIVE

        self.logger.info(
            f"LiveTradeExecutor initialized with broker: "
            f"{broker_config.get_broker_name()} "
            f"(timeout={self._timeout_config.order_timeout_seconds}s)"
        )

    # ============================================
    # Pending Order Processing (live-specific)
    # ============================================

    def _process_pending_orders(self) -> None:
        """
        Poll broker for pending order updates and handle timeouts.

        For each pending order:
        1. Check adapter for status update (filled/rejected/pending)
        2. On fill: call inherited _fill_open_order() / _fill_close_order()
        3. On rejection: record in _order_history
        4. On timeout: record rejection with BROKER_ERROR reason
        """
        # Early exit if nothing pending
        if not self._order_tracker.has_pending_orders():
            return

        # Poll broker for each pending order
        pending_orders = self._order_tracker.get_pending_orders()
        for pending in pending_orders:
            if not pending.broker_ref:
                continue

            response = self.broker.adapter.check_order_status(
                pending.broker_ref)
            self._handle_broker_response(pending, response)

        # Check for timeouts (orders that broker never responded to)
        timed_out = self._order_tracker.check_timeouts()
        for pending in timed_out:
            self._handle_timeout(pending)

    def _handle_broker_response(
        self,
        pending: PendingOrder,
        response: BrokerResponse,
    ) -> None:
        """
        Process a broker status response for a pending order.

        Args:
            pending: The pending order being checked
            response: Broker's current status response
        """
        if response.status == BrokerOrderStatus.FILLED:
            filled = self._order_tracker.mark_filled(
                broker_ref=pending.broker_ref,
                fill_price=response.fill_price,
                filled_lots=response.filled_lots,
            )
            if filled is None:
                return

            # Record pending outcome (latency = time from submission to fill)
            latency_ms = self._calculate_pending_latency_ms(filled)
            self._order_tracker.record_outcome(
                filled, PendingOrderOutcome.FILLED, latency_ms=latency_ms)

            # Call inherited fill processing
            if filled.order_action == PendingOrderAction.OPEN:
                self._fill_open_order(filled, fill_price=response.fill_price)
            elif filled.order_action == PendingOrderAction.CLOSE:
                self._fill_close_order(filled, fill_price=response.fill_price)

        elif response.status == BrokerOrderStatus.REJECTED:
            rejected = self._order_tracker.mark_rejected(
                broker_ref=pending.broker_ref,
                reason=response.rejection_reason or "broker_rejected",
            )
            if rejected is None:
                return

            # Record pending outcome
            latency_ms = self._calculate_pending_latency_ms(rejected)
            self._order_tracker.record_outcome(
                rejected, PendingOrderOutcome.REJECTED, latency_ms=latency_ms)

            # Record rejection in order history
            self._orders_rejected += 1
            rejection = create_rejection_result(
                order_id=rejected.pending_order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=f"Broker rejected: {response.rejection_reason or 'unknown'}",
            )
            self._order_history.append(rejection)

        # PENDING / PARTIALLY_FILLED: no action, keep polling

    def _handle_timeout(self, pending: PendingOrder) -> None:
        """
        Handle a timed-out order. Remove from tracker, record rejection.

        Args:
            pending: The timed-out pending order
        """
        # Try to cancel at broker
        if pending.broker_ref:
            try:
                self.broker.adapter.cancel_order(pending.broker_ref)
            except Exception as e:
                self.logger.warning(
                    f"Failed to cancel timed-out order {pending.pending_order_id}: {e}"
                )

        # Record pending outcome as TIMED_OUT
        latency_ms = self._calculate_pending_latency_ms(pending)
        self._order_tracker.record_outcome(
            pending, PendingOrderOutcome.TIMED_OUT, latency_ms=latency_ms)

        # Remove from tracker
        self._order_tracker.mark_rejected(
            broker_ref=pending.broker_ref,
            reason="order_timeout",
        )

        # Record timeout as rejection
        self._orders_rejected += 1
        rejection = create_rejection_result(
            order_id=pending.pending_order_id,
            reason=RejectionReason.BROKER_ERROR,
            message=f"Order timed out after {self._timeout_config.order_timeout_seconds}s",
        )
        self._order_history.append(rejection)

        self.logger.warning(
            f"Order {pending.pending_order_id} timed out "
            f"(broker_ref={pending.broker_ref})"
        )

    # ============================================
    # Order Submission (live-specific)
    # ============================================

    def open_order(self, request: OpenOrderRequest) -> OrderResult:
        """
        Send order to broker for execution.

        Validates parameters, sends to broker via adapter, tracks in
        LiveOrderTracker. MARKET and LIMIT orders supported.

        Args:
            request: OpenOrderRequest with all order parameters

        Returns:
            OrderResult with PENDING, EXECUTED, or REJECTED status
        """
        self._orders_sent += 1
        self._order_counter += 1
        order_id = self.portfolio.get_next_position_id(request.symbol)

        # Feature gate: only MARKET and LIMIT orders
        if request.order_type not in (OrderType.MARKET, OrderType.LIMIT):
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
                message=f"Order type {request.order_type.value} not supported in live",
            )
            self._order_history.append(result)
            return result

        # Validate order parameters
        is_valid, error = self.broker.validate_order(
            request.symbol, request.lots)
        if not is_valid:
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.INVALID_LOT_SIZE,
                message=error,
            )
            self._order_history.append(result)
            return result

        # Build order kwargs for adapter and tracker
        order_kwargs = {}
        if request.stop_loss is not None:
            order_kwargs["stop_loss"] = request.stop_loss
        if request.take_profit is not None:
            order_kwargs["take_profit"] = request.take_profit
        if request.comment:
            order_kwargs["comment"] = request.comment
        if request.order_type == OrderType.LIMIT and request.price is not None:
            order_kwargs["price"] = request.price

        # Send to broker via adapter (adapter keeps **kwargs — broker boundary)
        try:
            response = self.broker.adapter.execute_order(
                symbol=request.symbol,
                direction=request.direction,
                lots=request.lots,
                order_type=request.order_type,
                **order_kwargs,
            )
        except Exception as e:
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=f"Adapter execute_order() failed: {e}",
            )
            self._order_history.append(result)
            return result

        # Handle immediate rejection from broker
        if response.is_rejected:
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=f"Broker rejected: {response.rejection_reason or 'unknown'}",
            )
            self._order_history.append(result)
            return result

        # Handle immediate fill (some brokers fill market orders synchronously)
        if response.is_filled:
            # Track briefly then mark filled immediately
            self._order_tracker.submit_order(
                order_id=order_id,
                symbol=request.symbol,
                direction=request.direction,
                lots=request.lots,
                broker_ref=response.broker_ref,
                order_kwargs=order_kwargs,
            )
            filled = self._order_tracker.mark_filled(
                broker_ref=response.broker_ref,
                fill_price=response.fill_price,
                filled_lots=response.filled_lots,
            )
            if filled:
                self._fill_open_order(filled, fill_price=response.fill_price)

            result = OrderResult(
                order_id=order_id,
                status=OrderStatus.EXECUTED,
                executed_price=response.fill_price,
                executed_lots=response.filled_lots or request.lots,
                execution_time=datetime.now(timezone.utc),
                broker_order_id=response.broker_ref,
                metadata={"symbol": request.symbol,
                          "direction": request.direction.value},
            )
            self._order_history.append(result)
            return result

        # Order is pending — track and poll later
        self._order_tracker.submit_order(
            order_id=order_id,
            symbol=request.symbol,
            direction=request.direction,
            lots=request.lots,
            broker_ref=response.broker_ref,
            order_kwargs=order_kwargs,
        )

        result = OrderResult(
            order_id=order_id,
            status=OrderStatus.PENDING,
            broker_order_id=response.broker_ref,
            metadata={
                "symbol": request.symbol,
                "direction": request.direction.value,
                "lots": request.lots,
                "broker_ref": response.broker_ref,
            },
        )
        self._order_history.append(result)
        return result

    # ============================================
    # Close Commands (live-specific)
    # ============================================

    def close_position(
        self,
        position_id: str,
        lots: Optional[float] = None,
    ) -> OrderResult:
        """
        Send close order to broker.

        Args:
            position_id: Position to close
            lots: Lots to close (None = close all)

        Returns:
            OrderResult with PENDING or REJECTED status
        """
        # Check position exists in portfolio
        position = self.portfolio.get_position(position_id)
        if not position:
            return create_rejection_result(
                order_id=f"close_{position_id}",
                reason=RejectionReason.BROKER_ERROR,
                message=f"Position {position_id} not found",
            )

        # Send close to broker — close = reverse direction order
        close_direction = (
            OrderDirection.SHORT if position.direction == OrderDirection.LONG
            else OrderDirection.LONG
        )
        close_lots = lots if lots else position.lots

        try:
            response = self.broker.adapter.execute_order(
                symbol=position.symbol,
                direction=close_direction,
                lots=close_lots,
                order_type=OrderType.MARKET,
            )
        except Exception as e:
            return create_rejection_result(
                order_id=f"close_{position_id}",
                reason=RejectionReason.BROKER_ERROR,
                message=f"Adapter execute_order() failed on close: {e}",
            )

        # Handle immediate rejection
        if response.is_rejected:
            return create_rejection_result(
                order_id=f"close_{position_id}",
                reason=RejectionReason.BROKER_ERROR,
                message=f"Broker rejected close: {response.rejection_reason}",
            )

        # Handle immediate fill
        if response.is_filled:
            self._order_tracker.submit_close_order(
                position_id=position_id,
                broker_ref=response.broker_ref,
                close_lots=close_lots,
            )
            filled = self._order_tracker.mark_filled(
                broker_ref=response.broker_ref,
                fill_price=response.fill_price,
                filled_lots=response.filled_lots,
            )
            if filled:
                self._fill_close_order(filled, fill_price=response.fill_price)

            return OrderResult(
                order_id=position_id,
                status=OrderStatus.EXECUTED,
                executed_price=response.fill_price,
                executed_lots=close_lots,
                execution_time=datetime.now(timezone.utc),
                broker_order_id=response.broker_ref,
            )

        # Pending close — track
        self._order_tracker.submit_close_order(
            position_id=position_id,
            broker_ref=response.broker_ref,
            close_lots=close_lots,
        )

        return OrderResult(
            order_id=position_id,
            status=OrderStatus.PENDING,
            broker_order_id=response.broker_ref,
            executed_lots=close_lots,
            execution_time=datetime.now(timezone.utc),
            metadata={"awaiting_fill": True,
                      "broker_ref": response.broker_ref},
        )

    # ============================================
    # Limit Order Modification
    # ============================================

    def modify_limit_order(
        self,
        order_id: str,
        new_price: Union[float, _UnsetType] = UNSET,
        new_stop_loss: Union[float, None, _UnsetType] = UNSET,
        new_take_profit: Union[float, None, _UnsetType] = UNSET
    ) -> ModificationResult:
        """
        Modify a pending limit order via broker adapter.

        Resolves order_id to broker_ref via LiveOrderTracker, then calls
        adapter.modify_order() to modify the order at the broker.

        No local SL/TP validation — broker handles that server-side.
        TODO: When order lifecycle is lifted into AbstractTradeExecutor
        (#133 Step 4), local active limit/stop tracking will enable
        local shadow state updates after successful broker modify.

        Args:
            order_id: Pending limit order ID
            new_price: New limit price (UNSET=keep current)
            new_stop_loss: New SL level (UNSET=no change, None=remove)
            new_take_profit: New TP level (UNSET=no change, None=remove)

        Returns:
            ModificationResult with success status and rejection reason
        """
        # Resolve order_id → broker_ref via tracker
        broker_ref = self._order_tracker.get_broker_ref(order_id)
        if broker_ref is None:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.LIMIT_ORDER_NOT_FOUND)

        # Translate UNSET → None for adapter (adapter uses None=no change)
        adapter_price = None if isinstance(new_price, _UnsetType) else new_price
        adapter_sl = None if isinstance(new_stop_loss, _UnsetType) else new_stop_loss
        adapter_tp = None if isinstance(new_take_profit, _UnsetType) else new_take_profit

        # Call broker adapter
        try:
            response = self.broker.adapter.modify_order(
                broker_ref=broker_ref,
                new_price=adapter_price,
                new_stop_loss=adapter_sl,
                new_take_profit=adapter_tp,
            )
        except Exception as e:
            self.logger.warning(
                f"modify_limit_order failed for {order_id}: {e}"
            )
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.INVALID_PRICE)

        # Handle broker rejection
        if response.is_rejected:
            self.logger.warning(
                f"Broker rejected modify for {order_id}: "
                f"{response.rejection_reason}"
            )
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.INVALID_PRICE)

        self.logger.info(
            f"✏️ Limit order {order_id} modified at broker "
            f"(broker_ref={broker_ref})"
        )

        return ModificationResult(success=True)

    def modify_stop_order(
        self,
        order_id: str,
        new_stop_price: Union[float, _UnsetType] = UNSET,
        new_limit_price: Union[float, _UnsetType] = UNSET,
        new_stop_loss: Union[float, None, _UnsetType] = UNSET,
        new_take_profit: Union[float, None, _UnsetType] = UNSET
    ) -> ModificationResult:
        """
        Modify a pending stop order (not supported in live).

        Live executor does not manage stop orders locally — broker handles them.

        Args:
            order_id: Pending stop order ID
            new_stop_price: New trigger price (UNSET=keep current)
            new_limit_price: New limit price for STOP_LIMIT (UNSET=keep current)
            new_stop_loss: New SL level (UNSET=no change, None=remove)
            new_take_profit: New TP level (UNSET=no change, None=remove)

        Returns:
            ModificationResult (always NOT_FOUND — no local stop order queue)
        """
        return ModificationResult(
            success=False,
            rejection_reason=ModificationRejectionReason.STOP_ORDER_NOT_FOUND)

    def cancel_stop_order(self, order_id: str) -> bool:
        """
        Cancel an active stop order (not supported in live).

        Args:
            order_id: Order ID to cancel

        Returns:
            False (no local stop order queue)
        """
        return False

    def get_active_order_counts(self) -> Dict[str, int]:
        """
        Get counts of active orders by world.

        Returns:
            Dict with pending count from broker tracker
        """
        return {
            "latency_queue": self._order_tracker.get_pending_count(),
            "active_limits": 0,
            "active_stops": 0,
        }

    # ============================================
    # Pending Order Awareness
    # ============================================

    def has_pending_orders(self) -> bool:
        """Check if any orders are pending at broker."""
        return self._order_tracker.has_pending_orders()

    def has_pipeline_orders(self) -> bool:
        """Check if any orders are pending at broker (same as has_pending_orders for live)."""
        return self._order_tracker.has_pending_orders()

    def is_pending_close(self, position_id: str) -> bool:
        """Check if a specific position has a pending close order."""
        return self._order_tracker.is_pending_close(position_id)

    def get_pending_stats(self) -> PendingOrderStats:
        """
        Get aggregated pending order statistics from live order tracker.

        Returns:
            PendingOrderStats with ms-based latency metrics
        """
        return self._order_tracker.get_pending_stats()

    # ============================================
    # Helpers
    # ============================================

    @staticmethod
    def _calculate_pending_latency_ms(pending: PendingOrder) -> Optional[float]:
        """
        Calculate pending duration in milliseconds from submitted_at to now.

        Args:
            pending: Pending order with submitted_at timestamp

        Returns:
            Latency in ms, or None if submitted_at not set
        """
        if pending.submitted_at is None:
            return None
        elapsed = datetime.now(timezone.utc) - pending.submitted_at
        return elapsed.total_seconds() * 1000

    # ============================================
    # Cleanup
    # ============================================

    def close_all_remaining_orders(self, current_tick: int = 0) -> None:
        """
        Close all open positions at end of run.

        Two-phase cleanup (same pattern as TradeSimulator):
        1. Direct-fill open positions using synthetic PendingOrders.
           These bypass the pending order pipeline entirely — no pending
           created, no FORCE_CLOSED in statistics. This is an internal
           cleanup, not an algo-initiated action.
        2. clear_pending() catches genuine stuck-in-pipeline orders
           (e.g. broker hasn't confirmed a fill yet when session ends).
           These ARE real anomalies and correctly recorded as
           FORCE_CLOSED with reason="scenario_end".

        Args:
            current_tick: Not used in live mode (latency is time-based)
        """
        open_positions = self.get_open_positions()
        if open_positions:
            self.logger.warning(
                f"{len(open_positions)} positions remain open — direct-closing (no pending)"
            )
            # Direct fill via synthetic PendingOrder — bypasses pending pipeline
            for pos in open_positions:
                synthetic = self._order_tracker.create_synthetic_close_order(
                    pos.position_id)
                self._fill_close_order(
                    synthetic, close_reason=CloseReason.SCENARIO_END)

        # Catch genuine stuck-in-pipeline orders (real anomalies)
        self._order_tracker.clear_pending(reason="scenario_end")
