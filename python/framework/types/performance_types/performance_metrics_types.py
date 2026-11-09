"""
FiniexTestingIDE - Performance Metrics Types
Dataclasses for profiling and performance metrics: how the time between ticks is distributed.
How a scenario's time splits between workers, decision logic and coordination is derived on the
profiling report (`WorkerDecisionBreakdownRow`).

Architecture:
- Filled by batch_orchestrator during tick loop profiling
- Stored in ScenarioPerformanceStats
- Read by ProfilingSummary for visualization
"""

from dataclasses import dataclass


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
