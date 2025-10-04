"""
FiniexTestingIDE - Performance Log Worker
Tracks performance metrics for individual workers
"""

from typing import Dict, Any


class PerformanceLogWorker:
    """
    Performance logger for a single worker.

    Tracks call count, execution times (min/max/avg), and total time consumption.
    Designed for minimal overhead - just accumulates metrics.
    """

    def __init__(self, worker_type: str, worker_name: str):
        """
        Initialize performance logger for a worker.

        Args:
            worker_type: Worker type (e.g., "CORE/rsi")
            worker_name: Worker instance name (e.g., "RSI_M5")
        """
        self.worker_type = worker_type
        self.worker_name = worker_name

        # Performance metrics
        self.call_count = 0
        self.total_time_ms = 0.0
        self.min_time_ms = float('inf')
        self.max_time_ms = 0.0

    def record(self, execution_time_ms: float):
        """
        Record a single execution time.

        Args:
            execution_time_ms: Execution time in milliseconds
        """
        self.call_count += 1
        self.total_time_ms += execution_time_ms

        # Update min/max
        if execution_time_ms < self.min_time_ms:
            self.min_time_ms = execution_time_ms
        if execution_time_ms > self.max_time_ms:
            self.max_time_ms = execution_time_ms

    def get_avg_time_ms(self) -> float:
        """Get average execution time in milliseconds."""
        if self.call_count == 0:
            return 0.0
        return self.total_time_ms / self.call_count

    def get_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics snapshot.

        Returns:
            Dict with all performance metrics
        """
        return {
            "worker_type": self.worker_type,
            "worker_name": self.worker_name,
            "call_count": self.call_count,
            "total_time_ms": round(self.total_time_ms, 3),
            "avg_time_ms": round(self.get_avg_time_ms(), 3),
            "min_time_ms": round(self.min_time_ms, 3) if self.min_time_ms != float('inf') else 0.0,
            "max_time_ms": round(self.max_time_ms, 3),
        }

    def reset(self):
        """Reset all metrics to initial state."""
        self.call_count = 0
        self.total_time_ms = 0.0
        self.min_time_ms = float('inf')
        self.max_time_ms = 0.0
