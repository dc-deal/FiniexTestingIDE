"""
FiniexTestingIDE - Performance Metrics Types
Dataclasses for profiling and performance metrics: how the time between ticks is distributed,
and how a scenario's time splits between workers, decision logic and coordination.

Architecture:
- Filled by batch_orchestrator during tick loop profiling
- Stored in ScenarioPerformanceStats
- Read by ProfilingSummary for visualization
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class InterTickIntervalStats:
    """
    Distribution statistics for inter-tick time intervals.

    Percentile convention (industry standard in performance engineering):
    - P5 (5th percentile): 95% of intervals are longer than this.
      Represents the "fastest tick bursts" — the tightest time budget.
    - P95 (95th percentile): only 5% of intervals are longer than this.
      Represents quiet market phases with long gaps between ticks.

    Args:
        min_ms: Shortest interval between two consecutive ticks
        max_ms: Longest interval (after gap filtering)
        mean_ms: Average interval
        median_ms: Median interval (50th percentile)
        p5_ms: 5th percentile — fastest 5% of tick arrivals
        p95_ms: 95th percentile — slowest 5% of tick arrivals
        total_intervals: Total number of tick-to-tick intervals
        filtered_intervals: Intervals remaining after gap removal
        gaps_removed: Number of intervals excluded as session/weekend gaps
        gap_threshold_s: Threshold used for gap filtering (seconds)
    """
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p5_ms: float   # 5th percentile — fastest 5% of tick arrivals
    p95_ms: float  # 95th percentile — slowest 5% of tick arrivals
    total_intervals: int
    filtered_intervals: int
    gaps_removed: int
    gap_threshold_s: float


@dataclass
class WorkerDecisionBreakdown:
    """
    Detailed breakdown of worker_decision time.

    Shows exactly where time is spent in WorkerOrchestrator.process_tick():
    - Pure worker computation time
    - Decision logic computation time
    - Coordination overhead (the mystery!)

    This helps identify performance bottlenecks within the decision pipeline.
    """
    scenario_name: str

    # Total time (should match worker_decision from TickLoopProfile)
    total_time_ms: float
    total_ticks: int

    # Component breakdown
    worker_execution_ms: float       # Sum of all worker.compute() times
    decision_logic_ms: float         # DecisionLogic.compute() time
    coordination_overhead_ms: float  # Overhead (calculated as difference)

    # Per-worker details (from PerformanceLogCoordinator)
    worker_breakdown: Dict[str, float] = field(
        default_factory=dict)  # {worker_name: time_ms}

    # Percentages of total worker_decision time
    worker_execution_pct: float = 0.0
    decision_logic_pct: float = 0.0
    coordination_overhead_pct: float = 0.0

    # Derived metric (pure calculation). The "is overhead too high?" verdict is NOT here — it is a
    # decision produced by the post-run validator (#395, no decisions in reports).
    overhead_ratio: float = 0.0  # overhead / computation time

    def __post_init__(self):
        """Calculate derived metrics after initialization."""
        if self.total_time_ms > 0:
            self.worker_execution_pct = (
                self.worker_execution_ms / self.total_time_ms) * 100
            self.decision_logic_pct = (
                self.decision_logic_ms / self.total_time_ms) * 100
            self.coordination_overhead_pct = (
                self.coordination_overhead_ms / self.total_time_ms) * 100

        # Calculate overhead ratio (pure calc — no verdict)
        computation_time = self.worker_execution_ms + self.decision_logic_ms
        if computation_time > 0:
            self.overhead_ratio = self.coordination_overhead_ms / computation_time
