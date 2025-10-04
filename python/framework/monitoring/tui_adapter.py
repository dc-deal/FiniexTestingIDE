"""
FiniexTestingIDE - TUI Adapter (Pseudo-Endpoint)
Provides live performance metrics for future TUI implementation

NEW (V0.7): Prepares for Issue #1 (TUI Dashboard)

This adapter acts as a bridge between the execution engine and
a future TUI (Terminal User Interface). It provides:
- Live performance snapshots (every 300ms)
- Minimal overhead access to metrics
- Standardized data format for display
"""

from typing import Dict, Any, Optional, List
from python.framework.workers.worker_coordinator import WorkerCoordinator
from python.framework.batch_orchestrator import BatchOrchestrator


class TUIAdapter:
    """
    Adapter for TUI live monitoring.

    Provides standardized access to execution metrics with minimal overhead.
    Designed to be polled at 300ms intervals without impacting performance.

    Usage:
        adapter = TUIAdapter(orchestrator)

        # In TUI loop (every 300ms):
        metrics = adapter.get_live_metrics()
        tui.update(metrics)
    """

    def __init__(self, orchestrator: Optional[BatchOrchestrator] = None):
        """
        Initialize TUI adapter.

        Args:
            orchestrator: BatchOrchestrator instance (if available)
        """
        self.orchestrator = orchestrator
        self.worker_coordinator: Optional[WorkerCoordinator] = None
        self.refresh_rate_ms = 300  # Default TUI refresh rate

    def set_orchestrator(self, orchestrator: BatchOrchestrator):
        """
        Set the orchestrator to monitor.

        Args:
            orchestrator: BatchOrchestrator instance
        """
        self.orchestrator = orchestrator

    def set_worker_coordinator(self, coordinator: WorkerCoordinator):
        """
        Set the worker coordinator to monitor.

        Args:
            coordinator: WorkerCoordinator instance
        """
        self.worker_coordinator = coordinator

    def get_live_metrics(self) -> Dict[str, Any]:
        """
        Get live performance metrics.

        This method is optimized for frequent polling (300ms intervals).
        It returns a snapshot of current performance without expensive
        calculations.

        Returns:
            Dict with live metrics in TUI-friendly format
        """
        if not self.worker_coordinator:
            return self._get_empty_metrics()

        # Get performance snapshot (minimal overhead)
        snapshot = self.worker_coordinator.get_performance_snapshot()

        # Transform to TUI-friendly format
        return {
            "status": "running",
            "scenario": snapshot.get("scenario_name", "Unknown"),
            "progress": {
                "ticks_processed": snapshot.get("ticks_processed", 0),
                "ticks_total": None,  # Not available in current design
            },
            "workers": self._format_worker_metrics(snapshot),
            "decision_logic": self._format_decision_metrics(snapshot),
            "parallel": {
                "enabled": snapshot.get("parallel_mode", False),
                "time_saved_ms": self._get_parallel_time_saved(snapshot),
            },
            "timestamp": self._get_timestamp(),
        }

    def get_full_report(self) -> Dict[str, Any]:
        """
        Get comprehensive performance report.

        This is a more expensive operation intended for final reports,
        not live updates.

        Returns:
            Dict with comprehensive metrics
        """
        if not self.worker_coordinator:
            return self._get_empty_metrics()

        # Get full report from performance coordinator
        perf_log = self.worker_coordinator.performance_log
        return perf_log.get_full_report()

    def _format_worker_metrics(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Format worker metrics for TUI display.

        Args:
            snapshot: Performance snapshot

        Returns:
            List of worker metric dicts
        """
        worker_stats = snapshot.get("worker_statistics", {})
        workers = worker_stats.get("workers", {})

        formatted = []
        for worker_name, worker_perf in workers.items():
            formatted.append({
                "name": worker_name,
                "type": worker_perf.get("worker_type", "Unknown"),
                "calls": worker_perf.get("call_count", 0),
                "avg_ms": worker_perf.get("avg_time_ms", 0),
                "min_ms": worker_perf.get("min_time_ms", 0),
                "max_ms": worker_perf.get("max_time_ms", 0),
            })

        return formatted

    def _format_decision_metrics(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format decision logic metrics for TUI display.

        Args:
            snapshot: Performance snapshot

        Returns:
            Decision logic metrics dict
        """
        decision_stats = snapshot.get("decision_logic_statistics", {})

        return {
            "name": decision_stats.get("decision_logic_name", "Unknown"),
            "type": decision_stats.get("decision_logic_type", "Unknown"),
            "decisions": decision_stats.get("decision_count", 0),
            "avg_ms": decision_stats.get("avg_time_ms", 0),
            "min_ms": decision_stats.get("min_time_ms", 0),
            "max_ms": decision_stats.get("max_time_ms", 0),
        }

    def _get_parallel_time_saved(self, snapshot: Dict[str, Any]) -> float:
        """
        Extract parallel time saved from snapshot.

        Args:
            snapshot: Performance snapshot

        Returns:
            Time saved in milliseconds
        """
        worker_stats = snapshot.get("worker_statistics", {})
        parallel_stats = worker_stats.get("parallel_stats", {})
        return parallel_stats.get("total_time_saved_ms", 0.0)

    def _get_timestamp(self) -> str:
        """
        Get current timestamp.

        Returns:
            ISO format timestamp
        """
        from datetime import datetime
        return datetime.now().isoformat()

    def _get_empty_metrics(self) -> Dict[str, Any]:
        """
        Get empty metrics structure.

        Returns:
            Empty metrics dict
        """
        return {
            "status": "idle",
            "scenario": None,
            "progress": {
                "ticks_processed": 0,
                "ticks_total": None,
            },
            "workers": [],
            "decision_logic": {
                "name": None,
                "type": None,
                "decisions": 0,
                "avg_ms": 0,
            },
            "parallel": {
                "enabled": False,
                "time_saved_ms": 0,
            },
            "timestamp": self._get_timestamp(),
        }


class TUIMetricsFormatter:
    """
    Formats metrics for terminal display.

    Helper class for rendering metrics in terminal-friendly format.
    Will be used by actual TUI implementation.
    """

    @staticmethod
    def format_worker_table(workers: List[Dict[str, Any]]) -> str:
        """
        Format workers as ASCII table.

        Args:
            workers: List of worker metrics

        Returns:
            Formatted ASCII table string
        """
        if not workers:
            return "No workers active"

        lines = []
        lines.append(
            "┌─────────────────┬────────┬──────────┬─────────────────┐")
        lines.append(
            "│ Worker          │ Calls  │ Avg (ms) │ Range (ms)      │")
        lines.append(
            "├─────────────────┼────────┼──────────┼─────────────────┤")

        for worker in workers:
            name = worker['name'][:15].ljust(15)
            calls = str(worker['calls']).rjust(6)
            avg = f"{worker['avg_ms']:.3f}".rjust(8)
            min_val = worker['min_ms']
            max_val = worker['max_ms']
            range_str = f"{min_val:.2f}-{max_val:.2f}".ljust(15)

            lines.append(f"│ {name} │ {calls} │ {avg} │ {range_str} │")

        lines.append(
            "└─────────────────┴────────┴──────────┴─────────────────┘")

        return "\n".join(lines)

    @staticmethod
    def format_progress_bar(current: int, total: Optional[int], width: int = 40) -> str:
        """
        Format progress bar.

        Args:
            current: Current progress
            total: Total (None if unknown)
            width: Bar width in characters

        Returns:
            Formatted progress bar string
        """
        if total is None or total == 0:
            return f"[{'?' * width}] {current} ticks"

        progress = min(1.0, current / total)
        filled = int(width * progress)
        bar = "█" * filled + "░" * (width - filled)

        return f"[{bar}] {current}/{total} ({progress*100:.1f}%)"
