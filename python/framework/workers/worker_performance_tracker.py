# DATEI UMBENENNEN VON: performance_log_worker.py
# DATEI UMBENENNEN ZU:   worker_performance_tracker.py

"""
FiniexTestingIDE - Worker Performance Tracker
Tracks performance metrics for individual workers

"""


from python.framework.types.performance_stats_types import WorkerPerformanceStats


class WorkerPerformanceTracker:
    """
    Performance tracker for a single worker.

    Tracks execution count and timing metrics.
    Works internally with WorkerPerformanceStats dataclass.

    Args:
        worker_type: Worker type (e.g., "CORE/rsi")
        worker_name: Worker instance name (e.g., "rsi_fast")
    """

    def __init__(self, worker_type: str, worker_name: str):
        """
        Initialize performance tracker for worker.

        Args:
            worker_type: Worker type
            worker_name: Worker instance name
        """
        self.worker_type = worker_type
        self.worker_name = worker_name

        # Internal statistics dataclass
        self._stats = WorkerPerformanceStats(
            worker_type=worker_type,
            worker_name=worker_name,
            worker_call_count=0,
            worker_total_time_ms=0.0,
            worker_avg_time_ms=0.0,
            worker_min_time_ms=0.0,
            worker_max_time_ms=0.0
        )

        # Min/Max tracking (not in dataclass)
        self._min_time_ms = float('inf')
        self._max_time_ms = 0.0

    def record(self, execution_time_ms: float) -> None:
        """
        Record a single execution time.

        Args:
            execution_time_ms: Execution time in milliseconds
        """
        self._stats.worker_call_count += 1
        self._stats.worker_total_time_ms += execution_time_ms

        self._min_time_ms = min(self._min_time_ms, execution_time_ms)
        self._max_time_ms = max(self._max_time_ms, execution_time_ms)

    def get_stats(self) -> WorkerPerformanceStats:
        """
        Get performance statistics snapshot.

        Returns complete WorkerPerformanceStats dataclass with
        calculated avg/min/max values.

        Returns:
            WorkerPerformanceStats with all metrics
        """
        # Calculate avg
        if self._stats.worker_call_count > 0:
            self._stats.worker_avg_time_ms = round(
                self._stats.worker_total_time_ms / self._stats.worker_call_count, 3
            )

        # Fill min/max
        self._stats.worker_min_time_ms = round(
            self._min_time_ms if self._min_time_ms != float('inf') else 0.0, 3
        )
        self._stats.worker_max_time_ms = round(self._max_time_ms, 3)

        return self._stats

    def reset(self) -> None:
        """Reset all metrics to initial state."""
        self._stats = WorkerPerformanceStats(
            worker_type=self.worker_type,
            worker_name=self.worker_name,
            worker_call_count=0,
            worker_total_time_ms=0.0,
            worker_avg_time_ms=0.0,
            worker_min_time_ms=0.0,
            worker_max_time_ms=0.0
        )
        self._min_time_ms = float('inf')
        self._max_time_ms = 0.0
