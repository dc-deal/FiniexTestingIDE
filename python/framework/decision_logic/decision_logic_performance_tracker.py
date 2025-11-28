# DATEI UMBENENNEN VON: performance_log_decision_logic.py
# DATEI UMBENENNEN ZU:   decision_logic_performance_tracker.py

"""
FiniexTestingIDE - Decision Logic Performance Tracker
Tracks performance metrics for decision logic execution

- Works internally with DecisionLogicStats dataclass
- Returns complete DecisionLogicStats (signals + timing)
"""


from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.performance_stats_types import DecisionLogicStats


class DecisionLogicPerformanceTracker:
    """
    Performance tracker for decision logic.

    Tracks decision counts, signal distribution, and execution timing.
    Works internally with DecisionLogicStats dataclass.

    Args:
        decision_logic_type: Decision logic type (e.g., "CORE/aggressive_trend")
        decision_logic_name: Decision logic instance name (e.g., "AggressiveTrend")
    """

    def __init__(self, decision_logic_type: str, decision_logic_name: str):
        """
        Initialize performance tracker for decision logic.

        Args:
            decision_logic_type: Decision logic type
            decision_logic_name: Decision logic instance name
        """
        self.decision_logic_type = decision_logic_type
        self.decision_logic_name = decision_logic_name

        # Internal statistics dataclass
        self._stats = DecisionLogicStats(
            decision_logic_type=decision_logic_type, decision_logic_name=decision_logic_name)

        # Min/Max tracking (not in dataclass)
        self._min_time_ms = float('inf')
        self._max_time_ms = 0.0

    def record(self, execution_time_ms: float, decision: Decision) -> None:
        """
        Record a single execution time and decision.

        Args:
            execution_time_ms: Execution time in milliseconds
            decision: Decision object to track action counts
        """
        self._stats.decision_count += 1

        # Track signal type
        if decision.action == DecisionLogicAction.BUY:
            self._stats.buy_signals += 1
        elif decision.action == DecisionLogicAction.SELL:
            self._stats.sell_signals += 1
        elif decision.action == DecisionLogicAction.FLAT:
            self._stats.flat_signals += 1

        # Track timing
        self._stats.decision_total_time_ms += execution_time_ms
        self._min_time_ms = min(self._min_time_ms, execution_time_ms)
        self._max_time_ms = max(self._max_time_ms, execution_time_ms)

    def record_trade_requested(self) -> None:
        """
        Record that a trade was requested.

        Called from execute_decision when order_result exists.
        """
        self._stats.trades_requested += 1

    def get_stats(self) -> DecisionLogicStats:
        """
        Get complete statistics snapshot.

        Returns complete DecisionLogicStats dataclass with:
        - Signal counts
        - Trade requests
        - Performance timing (avg/min/max calculated)

        Returns:
            DecisionLogicStats with all metrics
        """
        # Calculate avg
        if self._stats.decision_count > 0:
            self._stats.decision_avg_time_ms = round(
                self._stats.decision_total_time_ms / self._stats.decision_count, 3
            )

        # Fill min/max
        self._stats.decision_min_time_ms = round(
            self._min_time_ms if self._min_time_ms != float('inf') else 0.0, 3
        )
        self._stats.decision_max_time_ms = round(self._max_time_ms, 3)

        return self._stats

    def reset(self) -> None:
        """Reset all metrics to initial state."""
        self._stats = DecisionLogicStats()
        self._min_time_ms = float('inf')
        self._max_time_ms = 0.0
