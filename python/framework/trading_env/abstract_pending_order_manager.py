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
from datetime import datetime, timezone
from typing import Dict, List, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
    PendingOrderOutcome,
)
from python.framework.types.pending_order_stats_types import (
    PendingOrderRecord,
    PendingOrderStats,
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
        self._fill_counter = 0
        self._pending_stats: PendingOrderStats = PendingOrderStats()

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

    # ============================================
    # Synthetic Order Factory
    # ============================================

    def create_synthetic_close_order(self, position_id: str) -> PendingOrder:
        """
        Create a PendingOrder for direct close — bypasses the latency pipeline.

        Used by close_all_remaining_orders() for clean end-of-scenario fills.
        The returned PendingOrder is NOT stored in _pending_orders and will
        therefore never appear in clear_pending() or statistics.

        Only sets the fields that _fill_close_order() actually needs:
        - pending_order_id = position_id (used as portfolio lookup key)
        - order_action = CLOSE

        Args:
            position_id: The position to close

        Returns:
            A minimal PendingOrder suitable for _fill_close_order()
        """
        return PendingOrder(
            pending_order_id=position_id,
            order_action=PendingOrderAction.CLOSE,
        )

    # ============================================
    # Pending Order Statistics
    # ============================================

    def record_outcome(
        self,
        pending_order: PendingOrder,
        outcome: PendingOrderOutcome,
        latency_ticks: Optional[int] = None,
        latency_ms: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> None:
        """
        Record a resolved pending order outcome for statistics.

        Called by the executor after determining the final outcome.
        Updates aggregated latency stats. Stores individual record
        only for anomalous outcomes (FORCE_CLOSED, TIMED_OUT).

        Args:
            pending_order: The resolved pending order
            outcome: How the pending phase ended
            latency_ticks: Pending duration in ticks (simulation)
            latency_ms: Pending duration in ms (live)
            reason: Why the force-close happened (e.g. "scenario_end", "manual_abort")
        """
        # Build anomaly record for FORCE_CLOSED / TIMED_OUT
        anomaly_record = None
        if outcome in (PendingOrderOutcome.FORCE_CLOSED, PendingOrderOutcome.TIMED_OUT):
            anomaly_record = PendingOrderRecord(
                order_id=pending_order.pending_order_id,
                action=pending_order.order_action,
                outcome=outcome,
                reason=reason,
                latency_ticks=latency_ticks,
                latency_ms=latency_ms,
                placed_at_tick=pending_order.placed_at_tick,
                submitted_at=pending_order.submitted_at,
            )

        self._pending_stats.record(
            outcome=outcome,
            latency_ticks=latency_ticks,
            latency_ms=latency_ms,
            anomaly_record=anomaly_record,
        )

    def get_pending_stats(self) -> PendingOrderStats:
        """
        Get aggregated pending order statistics.

        Returns:
            PendingOrderStats with latency metrics and anomaly records
        """
        return self._pending_stats

    # ============================================
    # Cleanup
    # ============================================

    def clear_pending(
        self,
        current_tick: Optional[int] = None,
        reason: str = "scenario_end"
    ) -> None:
        """
        Clear all pending orders. Records remaining orders as FORCE_CLOSED.

        Used at scenario end to prevent orders from leaking into next scenario.
        Orders still in queue are recorded as anomalies before clearing.
        These are genuine stuck-in-pipeline orders — not the normal
        end-of-scenario position closes (those use synthetic orders
        via create_synthetic_close_order and bypass the pipeline entirely).

        Args:
            current_tick: Current tick number for latency calculation (simulation).
                          None for live mode (uses wall-clock time).
            reason: Why the force-close happened (e.g. "scenario_end", "manual_abort")
        """
        if not self._pending_orders:
            return

        count = len(self._pending_orders)
        self.logger.warning(
            f"Clearing {count} pending order(s) — recording as FORCE_CLOSED (reason: {reason})"
        )

        # Record each remaining order as FORCE_CLOSED
        for pending in self._pending_orders.values():
            latency_ticks = None
            latency_ms = None

            # Simulation: tick-based latency
            if pending.placed_at_tick is not None and current_tick is not None:
                latency_ticks = current_tick - pending.placed_at_tick

            # Live: time-based latency
            if pending.submitted_at is not None:
                elapsed = datetime.now(timezone.utc) - pending.submitted_at
                latency_ms = elapsed.total_seconds() * 1000

            self.record_outcome(
                pending_order=pending,
                outcome=PendingOrderOutcome.FORCE_CLOSED,
                latency_ticks=latency_ticks,
                latency_ms=latency_ms,
                reason=reason,
            )

        self._pending_orders.clear()
