"""
FiniexTestingIDE - Performance Log Decision Logic
Tracks performance metrics for decision logic

FULLY TYPED: Returns DecisionLogicPerformanceStats dataclass instead of dict.
UNIQUE KEYWORDS: 'logic_*' for type/name, 'decision_*' for metrics.
"""

from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.performance_stats_types import DecisionLogicPerformanceStats


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

    def record(self, execution_time_ms: float, decision: Decision) -> None:
        """
        Record a single execution time.

        Args:
            execution_time_ms: Execution time in milliseconds
            decision: Decision object to track action counts
        """
        self.decision_count += 1
        if decision.action == DecisionLogicAction.BUY:
            self.buy_decision_count += 1
        if decision.action == DecisionLogicAction.BUY:
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

    def get_stats(self) -> DecisionLogicPerformanceStats:
        """
        Get performance statistics snapshot.

        FULLY TYPED: Returns DecisionLogicPerformanceStats dataclass.
        UNIQUE KEYWORDS: 'logic_*' for type/name, 'decision_*' for metrics.

        Returns:
            DecisionLogicPerformanceStats with all performance metrics
        """
        return DecisionLogicPerformanceStats(
            logic_type=self.decision_logic_type,
            logic_name=self.decision_logic_name,
            decision_count=self.decision_count,
            decision_buy_count=self.buy_decision_count,
            decision_sell_count=self.sell_decision_count,
            decision_total_time_ms=round(self.total_time_ms, 3),
            decision_avg_time_ms=round(self.get_avg_time_ms(), 3),
            decision_min_time_ms=round(
                self.min_time_ms, 3) if self.min_time_ms != float('inf') else 0.0,
            decision_max_time_ms=round(self.max_time_ms, 3)
        )

    def reset(self) -> None:
        """Reset all metrics to initial state."""
        self.decision_count = 0
        self.buy_decision_count = 0
        self.sell_decision_count = 0
        self.total_time_ms = 0.0
        self.min_time_ms = float('inf')
        self.max_time_ms = 0.0
