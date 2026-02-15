# ============================================
# python/framework/trading_env/abstract_pending_order_manager.py
# ============================================
"""
FiniexTestingIDE - Abstract Pending Order Manager
Base class for pending order storage and query across execution modes.

This provides the shared infrastructure that both simulation and live
trading need for tracking in-flight orders:
- Storage (store, remove, clear)
- Query (get all, filter by action, count)
- Convenience checks (has_pending, is_pending_close)

Subclasses add mode-specific behavior:
- SimulationLatencyManager: Seeded tick-based delays, deterministic fills
- LiveOrderTracker: Broker reference tracking, timeout detection (Horizon 2)

Architecture:
    AbstractPendingOrderManager
        │
        ├── SimulationLatencyManager (OrderLatencySimulator)
        │   - SeededDelayGenerator for deterministic delays
        │   - process_tick() → fills based on tick count
        │
        └── LiveOrderTracker (Horizon 2)
            - Broker reference tracking
            - mark_filled() / mark_rejected() from broker responses
            - check_timeouts() for unresponsive orders

Both managers share the same PendingOrder dataclass and the same
storage/query interface. The TradeExecutor subclasses delegate
has_pending_orders() and is_pending_close() to their respective manager.
"""
from abc import ABC
from typing import Dict, List, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.latency_simulator_types import PendingOrder, PendingOrderAction


class AbstractPendingOrderManager(ABC):
    """
    Base class for pending order management.

    Provides concrete storage and query methods shared by all execution modes.
    Subclasses implement mode-specific submission and fill detection logic.
    """

    def __init__(self, logger: AbstractLogger):
        self.logger = logger
        self._pending_orders: Dict[str, PendingOrder] = {}
        self._fill_counter = 0

    # ============================================
    # Storage (concrete — shared by all modes)
    # ============================================

    def store_order(self, pending_order: PendingOrder) -> None:
        """Store a pending order in the tracking cache."""
        self._pending_orders[pending_order.pending_order_id] = pending_order

    def remove_order(self, order_id: str) -> Optional[PendingOrder]:
        """
        Remove and return a pending order from the cache.

        Returns None if order_id not found (already removed or never existed).
        """
        return self._pending_orders.pop(order_id, None)

    # ============================================
    # Query (concrete — shared by all modes)
    # ============================================

    def get_pending_orders(
        self,
        filter_pending_action: Optional[PendingOrderAction] = None
    ) -> List[PendingOrder]:
        """
        Get pending orders, optionally filtered by action type.

        Args:
            filter_pending_action: Optional filter (OPEN or CLOSE).
                                   None returns all pending orders.

        Returns:
            List of PendingOrder objects matching the filter.
        """
        if filter_pending_action is None:
            return list(self._pending_orders.values())

        return [
            pending for pending in self._pending_orders.values()
            if pending.order_action == filter_pending_action
        ]

    def get_pending_count(self) -> int:
        """Get number of pending orders."""
        return len(self._pending_orders)

    def has_pending_orders(self) -> bool:
        """Are there any orders in flight?"""
        return len(self._pending_orders) > 0

    def is_pending_close(self, position_id: str) -> bool:
        """Is this specific position currently being closed?"""
        pending_closes = self.get_pending_orders(PendingOrderAction.CLOSE)
        return any(p.pending_order_id == position_id for p in pending_closes)

    def clear_pending(self) -> None:
        """
        Clear all pending orders.

        Used at scenario end to prevent orders from leaking into next scenario.
        """
        if self._pending_orders:
            count = len(self._pending_orders)
            self.logger.warning(
                f"⚠️ Clearing {count} pending order(s)"
            )
        self._pending_orders.clear()
