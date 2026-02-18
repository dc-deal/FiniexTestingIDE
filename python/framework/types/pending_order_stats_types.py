# ============================================
# python/framework/types/pending_order_stats_types.py
# ============================================
"""
FiniexTestingIDE - Pending Order Statistics Types
Data structures for tracking pending order lifecycle and latency metrics.

Used by AbstractPendingOrderManager for aggregated statistics,
and by reporting layer for summary display.

Two measurement units:
- Simulation: tick-based latency (placed_at_tick → resolved tick)
- Live: millisecond-based latency (submitted_at → resolved time)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from python.framework.types.latency_simulator_types import PendingOrderAction, PendingOrderOutcome


@dataclass
class PendingOrderRecord:
    """
    Single resolved pending order record.

    Only stored for anomalous outcomes (FORCE_CLOSED, TIMED_OUT).
    Normal fills/rejections are aggregated without individual records.

    Args:
        order_id: Order identifier
        action: OPEN or CLOSE
        outcome: How the pending phase ended
        reason: Why the force-close happened (e.g. "scenario_end", "manual_abort")
        latency_ticks: Pending duration in ticks (simulation)
        latency_ms: Pending duration in milliseconds (live)
        placed_at_tick: Tick when order entered pending (simulation)
        submitted_at: Time when order entered pending (live)
    """
    order_id: str
    action: PendingOrderAction
    outcome: PendingOrderOutcome
    reason: Optional[str] = None
    latency_ticks: Optional[int] = None
    latency_ms: Optional[float] = None
    placed_at_tick: Optional[int] = None
    submitted_at: Optional[datetime] = None


@dataclass
class PendingOrderStats:
    """
    Aggregated pending order statistics.

    Tracks latency metrics across all resolved pending orders.
    Maintains running aggregation (no individual records for normal outcomes).

    Anomalous orders (force_closed, timed_out) are stored individually
    in anomaly_orders for detailed reporting.

    Args:
        total_resolved: Total pending orders that left the queue
        total_filled: Orders resolved via normal fill
        total_rejected: Orders resolved via rejection (stress test, broker)
        total_timed_out: Orders that exceeded timeout (live)
        total_force_closed: Orders forcefully closed at scenario end
        avg_latency_ticks: Average pending duration in ticks (simulation)
        min_latency_ticks: Minimum pending duration in ticks
        max_latency_ticks: Maximum pending duration in ticks
        avg_latency_ms: Average pending duration in ms (live)
        min_latency_ms: Minimum pending duration in ms
        max_latency_ms: Maximum pending duration in ms
        anomaly_orders: Individual records for FORCE_CLOSED and TIMED_OUT
    """
    total_resolved: int = 0
    total_filled: int = 0
    total_rejected: int = 0
    total_timed_out: int = 0
    total_force_closed: int = 0

    # Tick-based latency (simulation)
    avg_latency_ticks: float = 0.0
    min_latency_ticks: Optional[int] = None
    max_latency_ticks: Optional[int] = None

    # Time-based latency (live)
    avg_latency_ms: float = 0.0
    min_latency_ms: Optional[float] = None
    max_latency_ms: Optional[float] = None

    # Individual records for anomalous outcomes only
    anomaly_orders: List[PendingOrderRecord] = field(default_factory=list)

    # Internal: running sum for average calculation (not serialized)
    _latency_ticks_sum: int = field(default=0, repr=False)
    _latency_ms_sum: float = field(default=0.0, repr=False)
    _latency_count: int = field(default=0, repr=False)

    def record(
        self,
        outcome: PendingOrderOutcome,
        latency_ticks: Optional[int] = None,
        latency_ms: Optional[float] = None,
        anomaly_record: Optional[PendingOrderRecord] = None
    ) -> None:
        """
        Record a resolved pending order outcome.

        Updates running aggregation counters and latency min/max/avg.
        Stores individual record only for anomalous outcomes.

        Args:
            outcome: How the pending phase ended
            latency_ticks: Pending duration in ticks (simulation)
            latency_ms: Pending duration in ms (live)
            anomaly_record: Individual record for FORCE_CLOSED/TIMED_OUT
        """
        self.total_resolved += 1

        # Count by outcome
        match outcome:
            case PendingOrderOutcome.FILLED:
                self.total_filled += 1
            case PendingOrderOutcome.REJECTED:
                self.total_rejected += 1
            case PendingOrderOutcome.TIMED_OUT:
                self.total_timed_out += 1
            case PendingOrderOutcome.FORCE_CLOSED:
                self.total_force_closed += 1

        # Update tick-based latency stats
        if latency_ticks is not None:
            self._latency_ticks_sum += latency_ticks
            self._latency_count += 1
            self.avg_latency_ticks = self._latency_ticks_sum / self._latency_count

            if self.min_latency_ticks is None or latency_ticks < self.min_latency_ticks:
                self.min_latency_ticks = latency_ticks
            if self.max_latency_ticks is None or latency_ticks > self.max_latency_ticks:
                self.max_latency_ticks = latency_ticks

        # Update ms-based latency stats
        if latency_ms is not None:
            self._latency_ms_sum += latency_ms
            self._latency_count += 1
            self.avg_latency_ms = self._latency_ms_sum / self._latency_count

            if self.min_latency_ms is None or latency_ms < self.min_latency_ms:
                self.min_latency_ms = latency_ms
            if self.max_latency_ms is None or latency_ms > self.max_latency_ms:
                self.max_latency_ms = latency_ms

        # Store anomaly record
        if anomaly_record is not None:
            self.anomaly_orders.append(anomaly_record)
