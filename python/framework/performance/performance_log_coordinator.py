"""
FiniexTestingIDE - Performance Log Coordinator
Coordinates performance logging for workers and decision logic

FULLY TYPED: Returns BatchPerformanceStats instead of dict.
REMOVED: get_full_report() - not needed anymore.
"""

from typing import Dict

from python.framework.performance.performance_log_worker import PerformanceLogWorker
from python.framework.performance.performance_log_decision_logic import PerformanceLogDecisionLogic
from python.framework.types.performance_stats_types import BatchPerformanceStats


class PerformanceLogCoordinator:
    """
    Coordinates performance logging for an entire scenario.

    Creates and manages PerformanceLogWorker instances for each worker
    and a PerformanceLogDecisionLogic instance for the decision logic.

    This is the central access point for performance metrics.
    """

    def __init__(self, parallel_workers: bool = False):
        """
        Initialize performance coordinator.

        Args:
            scenario_name: Name of the scenario being tracked
            parallel_workers: Whether workers run in parallel mode
        """
        self.parallel_workers = parallel_workers

        # Performance loggers
        self.worker_logs: Dict[str, PerformanceLogWorker] = {}
        self.decision_logic_log: PerformanceLogDecisionLogic = None

        # Parallel execution metrics
        self.parallel_time_saved_ms = 0.0
        self.ticks_processed = 0

    def create_worker_log(self, worker_type: str, worker_name: str) -> PerformanceLogWorker:
        """
        Create a performance logger for a worker.

        Args:
            worker_type: Worker type (e.g., "CORE/rsi")
            worker_name: Worker instance name (e.g., "RSI_M5")

        Returns:
            PerformanceLogWorker instance
        """
        if worker_name in self.worker_logs:
            return self.worker_logs[worker_name]

        log = PerformanceLogWorker(worker_type, worker_name)
        self.worker_logs[worker_name] = log
        return log

    def create_decision_logic_log(
        self,
        decision_logic_type: str,
        decision_logic_name: str
    ) -> PerformanceLogDecisionLogic:
        """
        Create a performance logger for decision logic.

        Args:
            decision_logic_type: Decision logic type (e.g., "CORE/simple_consensus")
            decision_logic_name: Decision logic instance name (e.g., "SimpleConsensus")

        Returns:
            PerformanceLogDecisionLogic instance
        """
        self.decision_logic_log = PerformanceLogDecisionLogic(
            decision_logic_type,
            decision_logic_name
        )
        return self.decision_logic_log

    def record_parallel_time_saved(self, time_saved_ms: float) -> None:
        """
        Record time saved by parallel execution.

        Args:
            time_saved_ms: Time saved in milliseconds
        """
        self.parallel_time_saved_ms += time_saved_ms

    def increment_ticks(self) -> None:
        """Increment tick counter."""
        self.ticks_processed += 1

    def get_snapshot(self) -> BatchPerformanceStats:
        """
        Get a live snapshot of all performance metrics.

        FULLY TYPED: Returns BatchPerformanceStats dataclass.
        This method is designed for minimal overhead so it can be called
        frequently (e.g., every 300ms for TUI updates).

        Returns:
            BatchPerformanceStats with complete performance snapshot
        """
        # Collect worker stats
        workers_dict = {}
        for worker_name, log in self.worker_logs.items():
            workers_dict[worker_name] = log.get_stats()

        # Calculate parallel stats
        avg_saved = 0.0
        if self.ticks_processed > 0:
            avg_saved = self.parallel_time_saved_ms / self.ticks_processed

        parallel_status = self._get_parallel_status(
            self.parallel_time_saved_ms)

        # Get decision logic stats
        decision_logic_stats = None
        if self.decision_logic_log:
            decision_logic_stats = self.decision_logic_log.get_stats()

        # Build BatchPerformanceStats
        return BatchPerformanceStats(
            ticks_processed=self.ticks_processed,
            parallel_mode=self.parallel_workers,
            total_workers=len(self.worker_logs),
            total_worker_calls=sum(
                log.call_count for log in self.worker_logs.values()),
            workers=workers_dict,
            parallel_time_saved_ms=round(self.parallel_time_saved_ms, 2),
            parallel_avg_saved_per_tick_ms=round(avg_saved, 3),
            parallel_status=parallel_status,
            decision_logic=decision_logic_stats
        )

    def _get_parallel_status(self, time_saved_ms: float) -> str:
        """
        Determine parallel execution status.

        Args:
            time_saved_ms: Total time saved

        Returns:
            Status string
        """
        if time_saved_ms > 0.01:
            return "✅ Faster"
        elif time_saved_ms < -0.01:
            return "⚠️ Slower"
        else:
            return "≈ Equal"

    def reset(self) -> None:
        """Reset all performance metrics."""
        for log in self.worker_logs.values():
            log.reset()
        if self.decision_logic_log:
            self.decision_logic_log.reset()
        self.parallel_time_saved_ms = 0.0
        self.ticks_processed = 0
