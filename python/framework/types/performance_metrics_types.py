"""
FiniexTestingIDE - Performance Metrics Types
Dataclasses for profiling and performance metrics

CREATED (New):
- Structured types for profiling data from batch_orchestrator
- Includes per-tick profiling, operation breakdowns, and scenario metrics
- Used by ProfilingSummary for reporting

Architecture:
- Filled by batch_orchestrator during tick loop profiling
- Stored in ScenarioPerformanceStats
- Read by ProfilingSummary for visualization
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class OperationProfile:
    """
    Profiling data for a single operation type.

    Tracks timing and call counts for operations like:
    - trade_simulator
    - bar_rendering
    - bar_history
    - worker_decision
    - order_execution
    - stats_update
    """
    operation_name: str
    total_time_ms: float  # Total time spent in milliseconds
    call_count: int  # Number of times called
    avg_time_ms: float  # Average time per call
    percentage: float  # Percentage of total tick time


@dataclass
class TickLoopProfile:
    """
    Complete profiling data for a scenario's tick loop.

    Contains:
    - Per-operation breakdowns
    - Total tick processing time
    - Performance percentages
    - Bottleneck identification
    """
    scenario_index: int
    scenario_name: str
    total_ticks: int

    # Per-operation profiles
    operations: List[OperationProfile] = field(default_factory=list)

    # Overall metrics
    total_time_ms: float = 0.0  # Total time for all ticks
    avg_time_per_tick_ms: float = 0.0  # Average time per tick

    # Derived metrics
    bottleneck_operation: Optional[str] = None  # Operation taking most time
    bottleneck_percentage: float = 0.0

    def __post_init__(self):
        """Calculate derived metrics after initialization."""
        if self.operations:
            # Find bottleneck
            slowest = max(self.operations, key=lambda op: op.percentage)
            self.bottleneck_operation = slowest.operation_name
            self.bottleneck_percentage = slowest.percentage

        # Calculate average per tick
        if self.total_ticks > 0:
            self.avg_time_per_tick_ms = self.total_time_ms / self.total_ticks


@dataclass
class ProfilingMetrics:
    """
    Aggregated profiling metrics across multiple scenarios.

    Used for:
    - Cross-scenario performance comparison
    - Identifying consistent bottlenecks
    - Performance optimization targets
    """
    # Individual scenario profiles
    scenario_profiles: List[TickLoopProfile] = field(default_factory=list)

    # Aggregated statistics
    total_scenarios: int = 0
    total_ticks_processed: int = 0
    total_execution_time_ms: float = 0.0
    avg_tick_time_ms: float = 0.0

    # Cross-scenario bottleneck analysis
    most_common_bottleneck: Optional[str] = None
    bottleneck_frequency: Dict[str, int] = field(default_factory=dict)

    def add_scenario_profile(self, profile: TickLoopProfile):
        """Add a scenario profile and update aggregates."""
        self.scenario_profiles.append(profile)
        self.total_scenarios += 1
        self.total_ticks_processed += profile.total_ticks
        self.total_execution_time_ms += profile.total_time_ms

        # Track bottleneck frequency
        if profile.bottleneck_operation:
            bottleneck = profile.bottleneck_operation
            self.bottleneck_frequency[bottleneck] = \
                self.bottleneck_frequency.get(bottleneck, 0) + 1

        # Update averages
        if self.total_ticks_processed > 0:
            self.avg_tick_time_ms = (
                self.total_execution_time_ms / self.total_ticks_processed
            )

        # Find most common bottleneck
        if self.bottleneck_frequency:
            self.most_common_bottleneck = max(
                self.bottleneck_frequency.items(),
                key=lambda x: x[1]
            )[0]


@dataclass
class ResourceMetrics:
    """
    System resource usage during scenario execution.

    Optional metrics that can be collected:
    - CPU usage (per core or average)
    - Memory usage (RAM)
    - Thread count
    - I/O statistics
    """
    scenario_index: int
    scenario_name: str

    # CPU metrics
    avg_cpu_percent: Optional[float] = None
    peak_cpu_percent: Optional[float] = None

    # Memory metrics
    avg_memory_mb: Optional[float] = None
    peak_memory_mb: Optional[float] = None

    # Threading metrics
    avg_thread_count: Optional[int] = None
    peak_thread_count: Optional[int] = None

    # Sampling info
    sample_count: int = 0
    sampling_interval_ms: float = 500.0  # How often samples were taken


@dataclass
class PerformanceSnapshot:
    """
    Point-in-time performance snapshot during execution.

    Used for:
    - Live progress display
    - Historical performance tracking
    - Performance degradation detection
    """
    timestamp: float  # Unix timestamp
    scenario_index: int
    ticks_processed: int

    # Current performance metrics
    current_tick_time_ms: float
    avg_tick_time_ms: float

    # Resource usage at this point
    cpu_percent: Optional[float] = None
    memory_mb: Optional[float] = None

    # Trading metrics
    portfolio_value: float = 0.0
    trades_count: int = 0


@dataclass
class WorkerDecisionBreakdown:
    """
    Detailed breakdown of worker_decision time.

    Shows exactly where time is spent in WorkerCoordinator.process_tick():
    - Pure worker computation time
    - Decision logic computation time
    - Coordination overhead (the mystery!)

    This helps identify performance bottlenecks within the decision pipeline.
    """
    scenario_index: int
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

    # Derived metrics
    overhead_ratio: float = 0.0  # overhead / computation time
    is_high_overhead: bool = False  # True if overhead > 50%

    def __post_init__(self):
        """Calculate derived metrics after initialization."""
        if self.total_time_ms > 0:
            self.worker_execution_pct = (
                self.worker_execution_ms / self.total_time_ms) * 100
            self.decision_logic_pct = (
                self.decision_logic_ms / self.total_time_ms) * 100
            self.coordination_overhead_pct = (
                self.coordination_overhead_ms / self.total_time_ms) * 100

        # Calculate overhead ratio
        computation_time = self.worker_execution_ms + self.decision_logic_ms
        if computation_time > 0:
            self.overhead_ratio = self.coordination_overhead_ms / computation_time
            self.is_high_overhead = self.overhead_ratio > 0.5  # Overhead > 50% of computation
