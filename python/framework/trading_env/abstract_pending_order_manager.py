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
- Pending order statistics (latency tracking, anomaly detection)

Subclasses add mode-specific behavior:
- SimulationLatencyManager: Seeded tick-based delays, deterministic fills
- LiveRequestProcessor: Broker reference tracking, timeout detection,
  Tier-3 orchestration (async submit worker thread, inbox/drain)

Architecture:
    AbstractPendingOrderManager
        │
        ├── SimulationLatencyManager (OrderLatencySimulator)
        │   - SeededDelayGenerator (utils/seeded_generators/) for deterministic delays
        │   - process_tick() → fills based on tick count
        │
        └── LiveRequestProcessor
            - Broker reference tracking
            - mark_filled() / mark_rejected() from broker responses
            - check_timeouts() for unresponsive orders
            - Async submit worker thread + drain_inbox routing

Both managers share the same PendingOrder dataclass and the same
storage/query interface. The TradeExecutor subclasses delegate
has_pending_orders() and is_pending_close() to their respective manager.
"""
import time
from abc import ABC
from typing import Dict, List, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
)


class AbstractPendingOrderManager(ABC):
    """
    Base class for pending order management.

    Provides concrete storage and query methods shared by all execution modes.
    Subclasses implement mode-specific submission and fill detection logic.
    """

    def __init__(self, logger: AbstractLogger):
        self.logger = logger
        self._pending_orders: Dict[str, PendingOrder] = {}

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

    def get_order(self, order_id: str) -> Optional[PendingOrder]:
        """
        The tracked pending with this id, without removing it.

        Args:
            order_id: Internal pending-order identifier

        Returns:
            The PendingOrder, or None when nothing tracks it
        """
        return self._pending_orders.get(order_id)

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
        """Is this specific position currently being closed?

        Matches on the ORDER id, which IS the position id for every close the strategy or
        the engine requests. A venue-held protective order (#503) is the exception — it
        carries its own id and names its position in `closes_position_id` — and it is
        deliberately NOT matched here. The obvious widening would be wrong in the more
        expensive direction: a protective order RESTS for the whole life of the position,
        so counting it as "a close is in flight" would make the algo believe it can never
        close, permanently. What this guard means is IN FLIGHT, not RESTING. #503 stage D
        owns the distinction, and the cancel-before-close ordering it needs.
        """
        pending_closes = self.get_pending_orders(PendingOrderAction.CLOSE)
        return any(p.pending_order_id == position_id for p in pending_closes)

    # ============================================
    # Pending Order Latency
    # ============================================

    @staticmethod
    def calculate_pending_latency_ms(pending: PendingOrder) -> Optional[float]:
        """
        Calculate a live order's pending duration in milliseconds, from submission to now.

        Measured on the MONOTONIC clock, never on the wall clock: NTP can step the
        wall clock backwards inside the submit-to-fill window, and the resulting
        negative latency lands in a min/max aggregate where it reads like a venue
        fault. A missing stamp yields None rather than a wall-clock substitute — an
        unmeasurable duration is reported as unmeasured, not as a wrong number.

        Args:
            pending: Pending order carrying the submission stamps

        Returns:
            Latency in ms, or None when the order carries no monotonic stamp
        """
        if pending.timing.submitted_monotonic is None:
            return None
        return (time.monotonic() - pending.timing.submitted_monotonic) * 1000

    # ============================================
    # Cleanup
    # ============================================

    def clear_pending(self, reason: str = 'scenario_end') -> List[PendingOrder]:
        """
        Clear all pending orders and hand them back, so the executor can book how each one ended.

        Used at scenario end to prevent orders from leaking into the next scenario. These are
        genuine stuck-in-pipeline orders. There are no end-of-scenario position closes any more
        — a position open at the end stays open and is reported as open (#492).

        Args:
            reason: Why they are cleared (e.g. "scenario_end", "manual_abort"), for the log line

        Returns:
            The orders cleared
        """
        if not self._pending_orders:
            return []

        self.logger.warning(
            f'Clearing {len(self._pending_orders)} pending order(s) still in flight '
            f'(reason: {reason})'
        )

        cleared = list(self._pending_orders.values())
        self._pending_orders.clear()
        return cleared
