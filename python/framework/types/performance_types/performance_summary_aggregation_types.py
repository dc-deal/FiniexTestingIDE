# ============================================
# Performance Aggregation Types
# ============================================

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class WorkerAggregateData:
    """
    Aggregated worker performance data across scenarios.

    Args:
        calls: Total number of worker calls
        total_time: Total execution time in milliseconds
        times: List of average times per scenario for calculating scenario averages
    """
    calls: int = 0
    total_time: float = 0.0
    times: List[float] = field(default_factory=list)


@dataclass
class DecisionAggregateData:
    """
    Aggregated decision logic performance data across scenarios.

    Args:
        calls: Total number of decisions made
        total_time: Total execution time in milliseconds
        times: List of average times per scenario for calculating scenario averages
    """
    calls: int = 0
    total_time: float = 0.0
    times: List[float] = field(default_factory=list)


@dataclass
class AggregatedPerformanceStats:
    """
    Complete aggregated performance statistics across all scenarios.

    Args:
        total_ticks: Total ticks processed across all scenarios
        total_decisions: Total decisions made across all scenarios
        total_signals: Total signals generated across all scenarios
        worker_aggregates: Worker performance data by worker name
        decision_aggregates: Decision logic performance data
    """
    total_ticks: int = 0
    total_decisions: int = 0
    total_signals: int = 0
    worker_aggregates: Dict[str, WorkerAggregateData] = field(
        default_factory=dict)
    decision_aggregates: DecisionAggregateData = field(
        default_factory=DecisionAggregateData)


@dataclass
class ScenarioBottleneckData:
    """
    Bottleneck data for slowest scenario.

    Args:
        name: Scenario name
        avg_time_per_tick: Average time per tick in milliseconds
        total_time: Total execution time in milliseconds
    """
    name: str
    avg_time_per_tick: float
    total_time: float


@dataclass
class WorkerBottleneckData:
    """
    Bottleneck data for slowest worker.

    Args:
        name: Worker name
        avg_time: Average execution time in milliseconds
        scenarios: List of (scenario_name, avg_time) tuples
    """
    name: str
    avg_time: float
    scenarios: List[Tuple[str, float]]


@dataclass
class DecisionLogicBottleneckData:
    """
    Bottleneck data for slowest decision logic.

    Args:
        name: Decision logic name
        avg_time: Average execution time in milliseconds
        scenarios: List of (scenario_name, avg_time) tuples
    """
    name: str
    avg_time: float
    scenarios: List[Tuple[str, float]]


@dataclass
class ParallelBottleneckData:
    """
    Bottleneck data for worst parallel efficiency.

    Args:
        name: Scenario name
        time_saved: Time saved (negative means parallel slower than sequential)
        status: Parallel execution status description
    """
    name: str
    time_saved: float
    status: str


@dataclass
class PerformanceBottlenecks:
    """
    Complete bottleneck analysis across all scenarios.

    Args:
        slowest_scenario: Data for slowest scenario (optional)
        slowest_worker: Data for slowest worker (optional)
        slowest_decision_logic: Data for slowest decision logic (optional)
        worst_parallel: Data for worst parallel efficiency (optional)
    """
    slowest_scenario: Optional[ScenarioBottleneckData] = None
    slowest_worker: Optional[WorkerBottleneckData] = None
    slowest_decision_logic: Optional[DecisionLogicBottleneckData] = None
    worst_parallel: Optional[ParallelBottleneckData] = None
