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
    signal_rate: float
    success: bool

    # Worker statistics
    worker_statistics: Dict[str, Any]

    # Decision logic
    decision_logic_name: str

    # Scenario contract
    scenario_contract: Dict[str, Any]

    # Optional: First 10 signals for inspection
    sample_signals: List[Dict] = field(default_factory=list)

    # NEW (C#003 Refactor): Portfolio & Trading Stats (per scenario)
    # Each scenario gets its own TradeSimulator, stats stored here
    portfolio_stats: Dict[str, Any] = field(default_factory=dict)
    execution_stats: Dict[str, Any] = field(default_factory=dict)
    cost_breakdown: Dict[str, Any] = field(default_factory=dict)


class PerformanceSummaryLog:
    """
    Thread-safe container for performance statistics across all scenarios.

    Usage:
        # In strategy_runner.py
        perf_log = PerformanceSummaryLog()

        # In BatchOrchestrator
        perf_log.add_scenario_stats(scenario_index=0, stats=...)

        # In reporting
        all_stats = perf_log.get_all_scenarios()
    """

    def __init__(self):
        """Initialize empty performance log."""
        self._scenarios: Dict[int, ScenarioPerformanceStats] = {}
        self._lock = threading.Lock()

        # Metadata
        self._total_scenarios = 0
        self._execution_time = 0.0
        self._success = True

    def set_metadata(self, total_scenarios: int, execution_time: float, success: bool):
        """
        Set batch-level metadata.

        Args:
            total_scenarios: Total number of scenarios
            execution_time: Total execution time
            success: Overall success status
        """
        with self._lock:
            self._total_scenarios = total_scenarios
            self._execution_time = execution_time
            self._success = success

    def add_scenario_stats(
        self,
        scenario_index: int,
        scenario_name: str,
        symbol: str,
        ticks_processed: int,
        signals_generated: int,
        signal_rate: float,
        worker_statistics: Dict[str, Any],
        decision_logic_name: str,
        scenario_contract: Dict[str, Any],
        sample_signals: List[Dict] = None,
        success: bool = True,
        # NEW: Portfolio stats from scenario-specific TradeSimulator
        portfolio_stats: Dict[str, Any] = None,
        execution_stats: Dict[str, Any] = None,
        cost_breakdown: Dict[str, Any] = None
    ):
        """
        Add performance stats for a scenario.

        Thread-safe - can be called from parallel workers.
        """
        stats = ScenarioPerformanceStats(
            scenario_index=scenario_index,
            scenario_name=scenario_name,
            symbol=symbol,
            ticks_processed=ticks_processed,
            signals_generated=signals_generated,
            signal_rate=signal_rate,
            success=success,
            worker_statistics=worker_statistics,
            decision_logic_name=decision_logic_name,
            scenario_contract=scenario_contract,
            sample_signals=sample_signals or [],
            # NEW: Store portfolio stats
            portfolio_stats=portfolio_stats or {},
            execution_stats=execution_stats or {},
            cost_breakdown=cost_breakdown or {}
        )

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
            Dict with total_scenarios, execution_time, success
        """
        with self._lock:
            return {
                'total_scenarios': self._total_scenarios,
                'execution_time': self._execution_time,
                'success': self._success
            }

    def get_scenarios_count(self) -> int:
        """Get number of scenarios recorded."""
        with self._lock:
            return len(self._scenarios)

    def is_complete(self) -> bool:
        """
        Check if all scenarios have reported.

        Returns:
            True if all scenarios recorded
        """
        with self._lock:
            return len(self._scenarios) == self._total_scenarios

    def clear(self):
        """Clear all recorded statistics."""
        with self._lock:
            self._scenarios.clear()
            self._total_scenarios = 0
            self._execution_time = 0.0
            self._success = True
