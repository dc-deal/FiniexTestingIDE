"""
FiniexTestingIDE - Performance Log Coordinator
Coordinates performance logging for workers and decision logic
"""

from typing import Dict, Any, List
from python.framework.performance.performance_log_worker import PerformanceLogWorker
from python.framework.performance.performance_log_decision_logic import PerformanceLogDecisionLogic


class PerformanceLogCoordinator:
    """
    Coordinates performance logging for an entire scenario.

    Creates and manages PerformanceLogWorker instances for each worker
    and a PerformanceLogDecisionLogic instance for the decision logic.

    This is the central access point for performance metrics.
    """

    def __init__(self, scenario_name: str, parallel_workers: bool = False):
        """
        Initialize performance coordinator.

        Args:
            scenario_name: Name of the scenario being tracked
            parallel_workers: Whether workers run in parallel mode
        """
        self.scenario_name = scenario_name
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

    def record_parallel_time_saved(self, time_saved_ms: float):
        """
        Record time saved by parallel execution.

        Args:
            time_saved_ms: Time saved in milliseconds
        """
        self.parallel_time_saved_ms += time_saved_ms

    def increment_ticks(self):
        """Increment tick counter."""
        self.ticks_processed += 1

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Get a live snapshot of all performance metrics.

        This method is designed for minimal overhead so it can be called
        frequently (e.g., every 300ms for TUI updates).

        Returns:
            Dict with complete performance snapshot
        """
        snapshot = {
            "scenario_name": self.scenario_name,
            "ticks_processed": self.ticks_processed,
            "parallel_mode": self.parallel_workers,
            "worker_statistics": {
                "total_workers": len(self.worker_logs),
                "total_calls": sum(log.call_count for log in self.worker_logs.values()),
                "workers": {}
            },
        }

        # Add worker stats
        for worker_name, log in self.worker_logs.items():
            snapshot["worker_statistics"]["workers"][worker_name] = log.get_stats()

        # Add parallel stats if applicable
        if self.parallel_workers:
            avg_saved = 0.0
            if self.ticks_processed > 0:
                avg_saved = self.parallel_time_saved_ms / self.ticks_processed

            snapshot["worker_statistics"]["parallel_stats"] = {
                "total_time_saved_ms": round(self.parallel_time_saved_ms, 2),
                "avg_saved_per_tick_ms": round(avg_saved, 3),
                "status": self._get_parallel_status(self.parallel_time_saved_ms)
            }

        # Add decision logic stats
        if self.decision_logic_log:
            snapshot["decision_logic_statistics"] = self.decision_logic_log.get_stats()

        return snapshot

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
            return "⚠️  Slower"
        else:
            return "≈ Equal"

    def get_full_report(self) -> Dict[str, Any]:
        """
        Get complete performance report.

        This is a more expensive operation that includes additional
        analysis and formatting. Use for final reports, not live updates.

        Returns:
            Dict with comprehensive performance data
        """
        report = self.get_snapshot()

        # Add summary statistics
        total_worker_time = sum(
            log.total_time_ms for log in self.worker_logs.values())
        total_decision_time = self.decision_logic_log.total_time_ms if self.decision_logic_log else 0.0

        report["summary"] = {
            "total_worker_time_ms": round(total_worker_time, 2),
            "total_decision_time_ms": round(total_decision_time, 2),
            "total_processing_time_ms": round(total_worker_time + total_decision_time, 2),
        }

        return report

    def reset(self):
        """Reset all performance metrics."""
        for log in self.worker_logs.values():
            log.reset()
        if self.decision_logic_log:
            self.decision_logic_log.reset()
        self.parallel_time_saved_ms = 0.0
        self.ticks_processed = 0
