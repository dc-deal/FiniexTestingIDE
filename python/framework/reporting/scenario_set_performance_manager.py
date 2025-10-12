"""
FiniexTestingIDE - Performance Summary Log
Thread-safe container for performance statistics across scenarios

Key Features:
- Stores performance stats for multiple scenarios
- Thread-safe for parallel execution
- Maintains original scenario order (by array index)
- Provides aggregation methods

Architecture:
- Created in strategy_runner.py
- Passed to BatchOrchestrator
- Filled by BatchOrchestrator._execute_single_scenario()
- Read by reporting classes

REFACTORED:
- Removed _total_scenarios (auto-calculated from len(_scenarios))
- set_metadata() no longer requires total_scenarios parameter
- get_metadata() calculates total_scenarios automatically
- Removed is_complete() (was never used)
- add_scenario_stats() simplified to 2 parameters (index + stats object)
"""

import threading
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class ScenarioPerformanceStats:
    """
    Performance statistics for a single scenario.

    Contains all performance data that was previously in batch_results dict.
    """
    # Scenario metadata
    scenario_index: int  # Original position in scenario array
    scenario_name: str
    symbol: str

    # Execution stats
    ticks_processed: int
    signals_generated: int
    signals_gen_buy: int
    signals_gen_sell: int
    signal_rate: float
    success: bool

    # Worker statistics
    worker_statistics: Dict[str, Any]

    # Decision logic
    decision_logic_name: str

    # Scenario requirement
    scenario_requirement: Dict[str, Any]

    # Optional: First 10 signals for inspection
    sample_signals: List[Dict] = field(default_factory=list)

    # Portfolio & Trading Stats (per scenario)
    # Each scenario gets its own TradeSimulator, stats stored here
    portfolio_stats: Dict[str, Any] = field(default_factory=dict)
    execution_stats: Dict[str, Any] = field(default_factory=dict)
    cost_breakdown: Dict[str, Any] = field(default_factory=dict)


class ScenarioSetPerformanceManager:
    """
    Thread-safe container for performance statistics across all scenarios.

    Usage:
        # In strategy_runner.py
        perf_log = ScenarioSetPerformanceManager()

        # In BatchOrchestrator
        stats = ScenarioPerformanceStats(...)
        perf_log.add_scenario_stats(scenario_index=0, stats=stats)

        # After batch execution
        perf_log.set_metadata(execution_time=10.5, success=True)

        # In reporting
        all_stats = perf_log.get_all_scenarios()
        metadata = perf_log.get_metadata()
    """

    def __init__(self):
        """Initialize empty performance log."""
        self._scenarios: Dict[int, ScenarioPerformanceStats] = {}
        self._lock = threading.Lock()

        # Metadata
        self._execution_time = 0.0
        self._success = True

    def set_metadata(self, execution_time: float, success: bool):
        """
        Set batch-level metadata.

        Args:
            execution_time: Total execution time
            success: Overall success status
        """
        with self._lock:
            self._execution_time = execution_time
            self._success = success

    def add_scenario_stats(
        self,
        scenario_index: int,
        stats: ScenarioPerformanceStats
    ):
        """
        Add pre-built scenario stats.

        Thread-safe - can be called from parallel workers.
        The stats object is built outside the lock for better performance,
        only the dict write is inside the lock.

        Args:
            scenario_index: Original scenario array index
            stats: Complete ScenarioPerformanceStats object
        """
        with self._lock:
            self._scenarios[scenario_index] = stats

    def get_all_scenarios(self) -> List[ScenarioPerformanceStats]:
        """
        Get all scenario stats in original order.

        CRITICAL: Returns scenarios sorted by scenario_index, not completion order!
        This ensures parallel execution doesn't scramble the output.

        Returns:
            List of ScenarioPerformanceStats sorted by original array index
        """
        with self._lock:
            # Sort by scenario_index to maintain original order
            sorted_scenarios = sorted(
                self._scenarios.values(),
                key=lambda s: s.scenario_index
            )
            return sorted_scenarios

    def get_scenario_by_index(self, scenario_index: int) -> Optional[ScenarioPerformanceStats]:
        """
        Get stats for specific scenario.

        Args:
            scenario_index: Original scenario array index

        Returns:
            ScenarioPerformanceStats or None if not found
        """
        with self._lock:
            return self._scenarios.get(scenario_index)

    def get_metadata(self) -> Dict[str, Any]:
        """
        Get batch-level metadata.

        Returns:
            Dict with total_scenarios (auto-calculated), execution_time, success
        """
        with self._lock:
            return {
                'total_scenarios': len(self._scenarios),
                'execution_time': self._execution_time,
                'success': self._success
            }

    def get_scenarios_count(self) -> int:
        """Get number of scenarios recorded."""
        with self._lock:
            return len(self._scenarios)

    def clear(self):
        """Clear all recorded statistics."""
        with self._lock:
            self._scenarios.clear()
            self._execution_time = 0.0
            self._success = True
