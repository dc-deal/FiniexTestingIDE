"""
FiniexTestingIDE - Performance Statistics Types
Type definitions for worker and decision logic performance tracking

2-LEVEL STRUCTURE:
- Level 1: Individual stats (WorkerPerformanceStats, DecisionLogicPerformanceStats)
- Level 2: Container (BatchPerformanceStats)

UNIQUE KEYWORDS with prefixes for easy searching:
- worker_* for worker-related fields
- decision_* for decision logic fields
- parallel_* for parallel execution fields
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class WorkerPerformanceStats:
    """
    Performance statistics for a single worker.

    All fields prefixed with 'worker_' for unique searchability.

    Attributes:
        worker_type: Worker type (e.g., "CORE/rsi")
        worker_name: Worker instance name (e.g., "RSI_M5")
        worker_call_count: Number of times worker was called
        worker_total_time_ms: Total execution time in milliseconds
        worker_avg_time_ms: Average execution time per call
        worker_min_time_ms: Minimum execution time
        worker_max_time_ms: Maximum execution time
    """
    worker_type: str
    worker_name: str
    worker_call_count: int
    worker_total_time_ms: float
    worker_avg_time_ms: float
    worker_min_time_ms: float
    worker_max_time_ms: float


@dataclass
class DecisionLogicPerformanceStats:
    """
    Performance statistics for decision logic.

    Logic fields prefixed with 'logic_', decision counts with 'decision_'.

    Attributes:
        logic_type: Decision logic type (e.g., "CORE/simple_consensus")
        logic_name: Decision logic instance name (e.g., "SimpleConsensus")
        decision_count: Total number of decisions made
        decision_buy_count: Number of BUY decisions
        decision_sell_count: Number of SELL decisions
        decision_total_time_ms: Total execution time in milliseconds
        decision_avg_time_ms: Average execution time per decision
        decision_min_time_ms: Minimum execution time
        decision_max_time_ms: Maximum execution time
    """
    logic_type: str
    logic_name: str
    decision_count: int
    decision_buy_count: int
    decision_sell_count: int
    decision_total_time_ms: float
    decision_avg_time_ms: float
    decision_min_time_ms: float
    decision_max_time_ms: float


@dataclass
class BatchPerformanceStats:
    """
    Complete performance statistics for a scenario batch execution.

    2-LEVEL STRUCTURE: Contains worker stats and decision logic stats.
    Parallel execution metrics are integrated (no nested object).

    This is the top-level container returned by WorkerCoordinator.get_statistics()
    and stored in ScenarioPerformanceStats.worker_statistics.

    Attributes:
        scenario_name: Name of the scenario
        ticks_processed: Number of ticks processed
        parallel_mode: Whether workers ran in parallel

        # Worker aggregates
        total_workers: Total number of workers
        total_worker_calls: Total calls across all workers
        workers: Dict mapping worker_name to WorkerPerformanceStats

        # Parallel execution (integrated, not nested)
        parallel_time_saved_ms: Total time saved by parallel execution
        parallel_avg_saved_per_tick_ms: Average time saved per tick
        parallel_status: Status string ("✅ Faster", "⚠️ Slower", "≈ Equal")

        # Decision logic (integrated, not nested)
        decision_logic: DecisionLogicPerformanceStats or None
    """
    scenario_name: str
    ticks_processed: int
    parallel_mode: bool

    # Worker aggregates
    total_workers: int
    total_worker_calls: int
    workers: Dict[str, WorkerPerformanceStats] = field(default_factory=dict)

    # Parallel execution metrics (integrated)
    parallel_time_saved_ms: float = 0.0
    parallel_avg_saved_per_tick_ms: float = 0.0
    parallel_status: str = ""

    # Decision logic
    decision_logic: Optional[DecisionLogicPerformanceStats] = None
