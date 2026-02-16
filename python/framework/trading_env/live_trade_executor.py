# ============================================
# python/framework/trading_env/live_trade_executor.py
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

Feature gating: Only MARKET orders supported (same as simulation MVP).
"""

from datetime import datetime, timezone
from typing import Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live_order_tracker import LiveOrderTracker
from python.framework.types.latency_simulator_types import PendingOrderAction
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
    create_rejection_result,
)


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
    ):
        """
        Initialize live trade executor.

        Args:
            broker_config: Broker configuration (must have live-capable adapter)
            initial_balance: Starting account balance
            account_currency: Account currency
            logger: Logger instance
            timeout_config: Timeout thresholds for order monitoring
        """
        super().__init__(
            broker_config=broker_config,
            initial_balance=initial_balance,
            account_currency=account_currency,
            logger=logger,
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

            response = self.broker.adapter.check_order_status(pending.broker_ref)
            self._handle_broker_response(pending, response)

        # Check for timeouts (orders that broker never responded to)
        timed_out = self._order_tracker.check_timeouts()
        for pending in timed_out:
            self._handle_timeout(pending)

    def _handle_broker_response(
        self,
        pending: 'PendingOrder',
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

            # Record rejection in order history
            self._orders_rejected += 1
            rejection = create_rejection_result(
                order_id=rejected.pending_order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=f"Broker rejected: {response.rejection_reason or 'unknown'}",
            )
            self._order_history.append(rejection)

        # PENDING / PARTIALLY_FILLED: no action, keep polling

    def _handle_timeout(self, pending: 'PendingOrder') -> None:
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

    def open_order(
        self,
        symbol: str,
        order_type: OrderType,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> OrderResult:
        """
        Send order to broker for execution.

        Validates parameters, sends to broker via adapter, tracks in
        LiveOrderTracker. Only MARKET orders supported (feature gating).

        Args:
            symbol: Trading symbol (e.g., "BTCUSD")
            order_type: Order type (only MARKET supported)
            direction: LONG or SHORT
            lots: Position size
            **kwargs: Additional parameters (stop_loss, take_profit, comment)

        Returns:
            OrderResult with PENDING or REJECTED status
        """
        self._orders_sent += 1
        self._order_counter += 1
        order_id = self.portfolio.get_next_position_id(symbol)

        # Feature gate: only market orders
        if order_type != OrderType.MARKET:
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
                message=f"Order type {order_type.value} not supported in live MVP",
            )
            self._order_history.append(result)
            return result

        # Validate order parameters
        is_valid, error = self.broker.validate_order(symbol, lots)
        if not is_valid:
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.INVALID_LOT_SIZE,
                message=error,
            )
            self._order_history.append(result)
            return result

        # Send to broker via adapter
        try:
            response = self.broker.adapter.execute_order(
                symbol=symbol,
                direction=direction,
                lots=lots,
                order_type=order_type,
                **kwargs,
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
                symbol=symbol,
                direction=direction,
                lots=lots,
                broker_ref=response.broker_ref,
                **kwargs,
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
                executed_lots=response.filled_lots or lots,
                execution_time=datetime.now(timezone.utc),
                broker_order_id=response.broker_ref,
                metadata={"symbol": symbol, "direction": direction.value},
            )
            self._order_history.append(result)
            return result

        # Order is pending — track and poll later
        self._order_tracker.submit_order(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            lots=lots,
            broker_ref=response.broker_ref,
            **kwargs,
        )

        result = OrderResult(
            order_id=order_id,
            status=OrderStatus.PENDING,
            broker_order_id=response.broker_ref,
            metadata={
                "symbol": symbol,
                "direction": direction.value,
                "lots": lots,
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
            metadata={"awaiting_fill": True, "broker_ref": response.broker_ref},
        )

    # ============================================
    # Pending Order Awareness
    # ============================================

    def has_pending_orders(self) -> bool:
        """Check if any orders are pending at broker."""
        return self._order_tracker.has_pending_orders()

    def is_pending_close(self, position_id: str) -> bool:
        """Check if a specific position has a pending close order."""
        return self._order_tracker.is_pending_close(position_id)

    # ============================================
    # Cleanup
    # ============================================

    def close_all_remaining_orders(self) -> None:
        """
        Close all open positions via broker at end of run.

        Sends close orders for all open positions, then attempts
        to process remaining pending orders.
        """
        open_positions = self.get_open_positions()
        if open_positions:
            self.logger.warning(
                f"{len(open_positions)} positions remain open — auto-closing via broker"
            )
            for pos in open_positions:
                self.close_position(position_id=pos.position_id)

        # Process any immediate fills from close orders
        if self._order_tracker.has_pending_orders():
            self._process_pending_orders()

        # Clear remaining
        self._order_tracker.clear_pending()
