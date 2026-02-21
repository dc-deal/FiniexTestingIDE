# ============================================
# python/framework/trading_env/live_order_tracker.py
# ============================================
"""
FiniexTestingIDE - Live Order Tracker
Time-based pending order management for live broker execution (Horizon 2).

Extends AbstractPendingOrderManager with broker reference tracking,
timeout detection, and fill/rejection marking from broker responses.

Architecture:
    AbstractPendingOrderManager  (storage, query, has_pending, is_pending_close)
        |
        +-- OrderLatencySimulator  (simulation: tick-based fill detection)
        |
        +-- LiveOrderTracker  (this class: time-based, broker-driven)
            - broker_ref tracking with O(1) index
            - submit_order() / submit_close_order() with timeout
            - mark_filled() / mark_rejected() from broker responses
            - check_timeouts() for unresponsive orders

Design:
    Broker responses arrive asynchronously. LiveTradeExecutor polls
    the adapter and calls mark_filled()/mark_rejected() on this tracker.
    The tracker returns the PendingOrder so the executor can call
    inherited _fill_open_order() / _fill_close_order() from the base.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_pending_order_manager import AbstractPendingOrderManager
from python.framework.types.latency_simulator_types import PendingOrder, PendingOrderAction
from python.framework.types.live_execution_types import TimeoutConfig
from python.framework.types.order_types import OrderDirection


class LiveOrderTracker(AbstractPendingOrderManager):
    """
    Live pending order manager with broker reference tracking.

    Extends AbstractPendingOrderManager with time-based tracking:
    - Orders stored with submitted_at, broker_ref, timeout_at
    - Broker ref index for O(1) lookup when broker responds
    - Timeout detection for unresponsive orders

    Inherited from AbstractPendingOrderManager:
    - Pending order storage (_pending_orders dict)
    - Query methods (get_pending_orders, get_pending_count)
    - Convenience checks (has_pending_orders, is_pending_close)
    - Cleanup (clear_pending)
    """

    def __init__(self, logger: AbstractLogger, timeout_config: TimeoutConfig):
        """
        Initialize live order tracker.

        Args:
            logger: Logger instance
            timeout_config: Timeout thresholds for order monitoring
        """
        super().__init__(logger)
        self._timeout_config = timeout_config

        # Broker ref → order_id index for O(1) lookup
        self._broker_ref_index: Dict[str, str] = {}

    # ============================================
    # Order Submission (live-specific)
    # ============================================

    def submit_order(
        self,
        order_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        broker_ref: str,
        order_kwargs: Optional[Dict] = None,
    ) -> str:
        """
        Track a submitted OPEN order with broker reference.

        Called after adapter.execute_order() returns a broker_ref.
        Order enters PENDING state and is monitored for fill/timeout.

        Args:
            order_id: Internal order identifier
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Position size
            broker_ref: Broker's order reference ID
            order_kwargs: Optional order parameters (stop_loss, take_profit, comment)

        Returns:
            order_id for chaining
        """
        now = datetime.now(timezone.utc)
        timeout_at = now + \
            timedelta(seconds=self._timeout_config.order_timeout_seconds)

        pending = PendingOrder(
            pending_order_id=order_id,
            order_action=PendingOrderAction.OPEN,
            # Live fields
            submitted_at=now,
            broker_ref=broker_ref,
            timeout_at=timeout_at,
            # Order details
            symbol=symbol,
            direction=direction,
            lots=lots,
            entry_time=now,
            order_kwargs=order_kwargs or {},
        )

        # Store in inherited storage
        self.store_order(pending)

        # Index by broker ref for O(1) lookup
        self._broker_ref_index[broker_ref] = order_id

        self.logger.info(
            f"Live order tracked: {order_id} (broker_ref={broker_ref}) "
            f"timeout_at={timeout_at.isoformat()}"
        )

        return order_id

    def submit_close_order(
        self,
        position_id: str,
        broker_ref: str,
        close_lots: Optional[float] = None,
    ) -> str:
        """
        Track a submitted CLOSE order with broker reference.

        Args:
            position_id: Position to close (used as order_id)
            broker_ref: Broker's order reference ID
            close_lots: Lots to close (None = close all)

        Returns:
            position_id for chaining
        """
        now = datetime.now(timezone.utc)
        timeout_at = now + \
            timedelta(seconds=self._timeout_config.order_timeout_seconds)

        pending = PendingOrder(
            pending_order_id=position_id,
            order_action=PendingOrderAction.CLOSE,
            # Live fields
            submitted_at=now,
            broker_ref=broker_ref,
            timeout_at=timeout_at,
            # Close details
            close_lots=close_lots,
        )

        self.store_order(pending)
        self._broker_ref_index[broker_ref] = position_id

        self.logger.info(
            f"Live close order tracked: {position_id} (broker_ref={broker_ref})"
        )

        return position_id

    # ============================================
    # Broker Response Handling
    # ============================================

    def mark_filled(
        self,
        broker_ref: str,
        fill_price: float,
        filled_lots: float,
    ) -> Optional[PendingOrder]:
        """
        Mark order as filled by broker. Removes from pending.

        Called when adapter.check_order_status() returns FILLED.
        Returns the PendingOrder so LiveTradeExecutor can call
        inherited _fill_open_order() / _fill_close_order().

        Args:
            broker_ref: Broker's order reference ID
            fill_price: Broker's execution price
            filled_lots: Actual filled volume

        Returns:
            PendingOrder if found, None if broker_ref unknown
        """
        order_id = self._broker_ref_index.pop(broker_ref, None)
        if order_id is None:
            self.logger.warning(
                f"mark_filled: unknown broker_ref={broker_ref}"
            )
            return None

        pending = self.remove_order(order_id)
        if pending is None:
            self.logger.warning(
                f"mark_filled: order_id={order_id} not in pending cache"
            )
            return None

        self.logger.info(
            f"Order filled: {order_id} at {fill_price:.5f} "
            f"({filled_lots} lots, broker_ref={broker_ref})"
        )

        return pending

    def mark_rejected(
        self,
        broker_ref: str,
        reason: str,
    ) -> Optional[PendingOrder]:
        """
        Mark order as rejected by broker. Removes from pending.

        Called when adapter.check_order_status() returns REJECTED.
        Returns the PendingOrder so LiveTradeExecutor can record
        the rejection in _order_history.

        Args:
            broker_ref: Broker's order reference ID
            reason: Broker's rejection reason

        Returns:
            PendingOrder if found, None if broker_ref unknown
        """
        order_id = self._broker_ref_index.pop(broker_ref, None)
        if order_id is None:
            self.logger.warning(
                f"mark_rejected: unknown broker_ref={broker_ref}"
            )
            return None

        pending = self.remove_order(order_id)
        if pending is None:
            self.logger.warning(
                f"mark_rejected: order_id={order_id} not in pending cache"
            )
            return None

        self.logger.warning(
            f"Order rejected: {order_id} reason={reason} "
            f"(broker_ref={broker_ref})"
        )

        return pending

    # ============================================
    # Timeout Detection
    # ============================================

    def check_timeouts(self) -> List[PendingOrder]:
        """
        Return orders that have exceeded their timeout threshold.

        Does NOT remove them from pending — caller decides how to handle
        (retry, cancel, escalate).

        Returns:
            List of PendingOrder objects past timeout_at
        """
        now = datetime.now(timezone.utc)
        timed_out = []

        for pending in self._pending_orders.values():
            if pending.timeout_at and pending.timeout_at <= now:
                timed_out.append(pending)

        return timed_out

    # ============================================
    # Broker Reference Lookup
    # ============================================

    def get_by_broker_ref(self, broker_ref: str) -> Optional[PendingOrder]:
        """
        Look up pending order by broker reference.

        O(1) lookup via broker_ref index.

        Args:
            broker_ref: Broker's order reference ID

        Returns:
            PendingOrder if found, None otherwise
        """
        order_id = self._broker_ref_index.get(broker_ref)
        if order_id is None:
            return None
        return self._pending_orders.get(order_id)

    # ============================================
    # Cleanup (override to also clear index)
    # ============================================

    def clear_pending(
        self,
        current_tick: Optional[int] = None,
        reason: str = "scenario_end"
    ) -> None:
        """Clear all pending orders and broker ref index."""
        super().clear_pending(current_tick=current_tick, reason=reason)
        self._broker_ref_index.clear()
