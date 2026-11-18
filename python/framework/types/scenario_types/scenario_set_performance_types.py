"""
FiniexTestingIDE - Scenario Performance Types
Type definitions for scenario execution statistics

FULLY TYPED: Uses dataclasses from trading_env_types instead of generic dicts.
ProfilingData now fully typed instead of Dict[str, Any]
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from python.framework.types.performance_types.performance_metrics_types import (
    InterTickIntervalStats,
)

# Operations that are the intended hot path (strategy work) — a high share here is GOOD, not a
# bottleneck. Single source of truth for the profiling presenter + the post-run advisory validator.
EXPECTED_OPERATIONS = {'worker_decision', 'order_execution'}

# The profiling keys that time whole instants rather than one operation: every instant the algo
# was not clipped out of, and apart from it the instants the algo computed in (#460). A reader
# that lists operations leaves them out — a total is no operation and would top every listing.
PROFILE_TOTAL_KEYS = frozenset({'total_per_tick', 'total_per_algo_pass'})


@dataclass
class OperationTiming:
    """
    Timing data for a single operation in event loop.

    Represents profiling data for one operation (e.g., 'worker_decision',
    'bar_rendering') collected during scenario execution.

    Attributes:
        total_time_ms: Total time spent in this operation (milliseconds)
        call_count: Number of times this operation was called
    """
    total_time_ms: float
    call_count: int

    @property
    def avg_time_ms(self) -> float:
        """
        Average time per call.

        Returns:
            Average time in milliseconds, or 0.0 if no calls
        """
        return self.total_time_ms / self.call_count if self.call_count > 0 else 0.0


@dataclass
class ProfilingData:
    """
    Performance profiling data from scenario execution.

    Built AFTER event loop completes (zero overhead during ticks).
    Contains timing for each operation in the event loop.

    Operations tracked:
    - trade_simulator: Price updates
    - bar_rendering: OHLC bar construction
    - bar_history: Historical bar retrieval
    - worker_decision: Worker processing + decision logic
    - order_execution: Order placement and execution
    - stats_update: Statistics updates

    Attributes:
        operations: Map of operation_name -> OperationTiming
        total_per_tick_ms: Total time per tick across all operations
        total_per_algo_pass_ms: The time of the instants the algo computed in (#460)
        algo_passes: How many such instants were timed
    """
    operations: Dict[str, OperationTiming]
    total_per_tick_ms: float
    interval_stats: Optional[InterTickIntervalStats] = None
    # Total ticks in loop (including clipped). 0 = no clipping active.
    ticks_total: int = 0
    total_per_algo_pass_ms: float = 0.0
    algo_passes: int = 0

    def get_operation_time(self, operation_name: str) -> float:
        """
        Get total time for operation.

        Args:
            operation_name: Name of operation (e.g., 'worker_decision')

        Returns:
            Total time in milliseconds, or 0.0 if operation not found
        """
        timing = self.operations.get(operation_name)
        return timing.total_time_ms if timing else 0.0

    def avg_per_algo_pass_ms(self) -> Optional[float]:
        """
        The time of one algo pass: an instant the algo computed in, timed whole (#460).

        Returns:
            Milliseconds per pass; None where the algo never computed
        """
        return self.total_per_algo_pass_ms / self.algo_passes if self.algo_passes > 0 else None

    @classmethod
    def from_dicts(
        cls,
        profile_times: Dict[str, float],
        profile_counts: Dict[str, int],
        inter_tick_intervals_ms: Optional[List[float]] = None,
        gap_threshold_s: float = 300.0,
        ticks_total: int = 0
    ) -> 'ProfilingData':
        """
        Build ProfilingData from raw dict data (AFTER loop).

        This is called ONCE after event loop completes.
        Zero overhead during tick processing.

        Args:
            profile_times: Map of operation_name -> total_time_ms
            profile_counts: Map of operation_name -> call_count
            inter_tick_intervals_ms: Raw inter-tick intervals in milliseconds
            gap_threshold_s: Threshold for filtering session/weekend gaps (seconds)
            ticks_total: Total ticks in loop including clipped (0 = no clipping)

        Returns:
            ProfilingData instance
        """
        # Extract the totals (special keys) — non-destructive read: the input
        # dict is left intact so the same profile_times can be derived more than
        # once (e.g. by multiple report consumers), without depending on call order.
        total_per_tick = profile_times.get('total_per_tick', 0.0)
        total_per_algo_pass = profile_times.get('total_per_algo_pass', 0.0)

        # Build operations map (exclude the special keys: totals over the operations, not one)
        operations = {
            name: OperationTiming(
                total_time_ms=time_ms,
                call_count=profile_counts.get(name, 0)
            )
            for name, time_ms in profile_times.items()
            if name not in PROFILE_TOTAL_KEYS
        }

        # Build inter-tick interval stats
        interval_stats = None
        if inter_tick_intervals_ms:
            interval_stats = cls._compute_interval_stats(
                inter_tick_intervals_ms, gap_threshold_s
            )

        return cls(
            operations=operations,
            total_per_tick_ms=total_per_tick,
            interval_stats=interval_stats,
            ticks_total=ticks_total,
            total_per_algo_pass_ms=total_per_algo_pass,
            algo_passes=profile_counts.get('total_per_algo_pass', 0)
        )

    @staticmethod
    def _compute_interval_stats(
        intervals_ms: List[float],
        gap_threshold_s: float
    ) -> Optional[InterTickIntervalStats]:
        """
        Compute distribution statistics from raw inter-tick intervals.

        Filters out session/weekend gaps exceeding the threshold,
        then computes min, max, mean, median, P5, P95.

        Args:
            intervals_ms: Raw intervals in milliseconds
            gap_threshold_s: Gaps longer than this (seconds) are excluded

        Returns:
            InterTickIntervalStats or None if no valid intervals remain
        """
        total_intervals = len(intervals_ms)
        gap_threshold_ms = gap_threshold_s * 1000

        # Filter out session/weekend gaps
        filtered = [x for x in intervals_ms if x <= gap_threshold_ms]
        gaps_removed = total_intervals - len(filtered)

        if not filtered:
            return None

        arr = np.array(filtered)
        return InterTickIntervalStats(
            min_ms=float(np.min(arr)),
            max_ms=float(np.max(arr)),
            mean_ms=float(np.mean(arr)),
            median_ms=float(np.median(arr)),
            p5_ms=float(np.percentile(arr, 5)),
            p95_ms=float(np.percentile(arr, 95)),
            total_intervals=total_intervals,
            filtered_intervals=len(filtered),
            gaps_removed=gaps_removed,
            gap_threshold_s=gap_threshold_s
        )
