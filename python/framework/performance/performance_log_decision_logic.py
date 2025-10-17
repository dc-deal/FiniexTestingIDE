"""
FiniexTestingIDE - Performance Log Decision Logic
Tracks performance metrics for decision logic
"""

from typing import Dict, Any

from python.framework.types.global_types import Decision


class PerformanceLogDecisionLogic:
    """
    Performance logger for decision logic.

    Tracks decision count, execution times (min/max/avg), and total time consumption.
    Designed for minimal overhead - just accumulates metrics.
    """

    def __init__(self, decision_logic_type: str, decision_logic_name: str):
        """
        Initialize performance logger for decision logic.

        Args:
            decision_logic_type: Decision logic type (e.g., "CORE/simple_consensus")
            decision_logic_name: Decision logic instance name (e.g., "SimpleConsensus")
        """
        self.decision_logic_type = decision_logic_type
        self.decision_logic_name = decision_logic_name

        # Performance metrics
        self.decision_count = 0
        self.buy_decision_count = 0
        self.sell_decision_count = 0
        self.total_time_ms = 0.0
        self.min_time_ms = float('inf')
        self.max_time_ms = 0.0

    def record(self, execution_time_ms: float, decision: Decision):
        """
        Record a single execution time.

        Args:
            execution_time_ms: Execution time in milliseconds
        """
        self.decision_count += 1
        if decision.action == "BUY":
            self.buy_decision_count += 1
        if decision.action == "SELL":
            self.sell_decision_count += 1
        self.total_time_ms += execution_time_ms

        # Update min/max
        if execution_time_ms < self.min_time_ms:
            self.min_time_ms = execution_time_ms
        if execution_time_ms > self.max_time_ms:
            self.max_time_ms = execution_time_ms

    def get_avg_time_ms(self) -> float:
        """Get average execution time in milliseconds."""
        if self.decision_count == 0:
            return 0.0
        return self.total_time_ms / self.decision_count

    def get_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics snapshot.

        Returns:
            Dict with all performance metrics
        """
        return {
            "decision_logic_type": self.decision_logic_type,
            "decision_logic_name": self.decision_logic_name,
            "decision_count": self.decision_count,
            "buy_decision_count": self.buy_decision_count,
            "sell_decision_count": self.sell_decision_count,
            "total_time_ms": round(self.total_time_ms, 3),
            "avg_time_ms": round(self.get_avg_time_ms(), 3),
            "min_time_ms": round(self.min_time_ms, 3) if self.min_time_ms != float('inf') else 0.0,
            "max_time_ms": round(self.max_time_ms, 3),
        }

    def reset(self):
        """Reset all metrics to initial state."""
        self.decision_count = 0
        self.total_time_ms = 0.0
        self.min_time_ms = float('inf')
        self.max_time_ms = 0.0
