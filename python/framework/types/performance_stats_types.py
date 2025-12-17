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

from dataclasses import dataclass
from typing import Optional

from python.framework.types.backtesting_metadata_types import BacktestingMetadata


@dataclass
class WorkerPerformanceStats:
    """
    Performance statistics for a single worker.

    All fields prefixed with 'worker_' for unique searchability.
    """
    worker_type: str
    worker_name: str
    worker_call_count: int
    worker_total_time_ms: float
    worker_avg_time_ms: float
    worker_min_time_ms: float
    worker_max_time_ms: float


@dataclass
class DecisionLogicStats:
    """
    Complete statistics for decision logic execution.

    Tracks signal counts AND performance timing.
    Used for serialization in ProcessTickLoopResult.

    Contains:
    - Signal counts (buy/sell/flat)
    - Trade execution requests
    - Performance metrics (timing)
    """
    # decision type
    decision_logic_type: str = ''  # type -> CORE/agresseve_trend
    decision_logic_name: str = ''  # name -> agresseve_trend

    # Signal counts
    decision_count: int = 0
    buy_signals: int = 0
    sell_signals: int = 0
    flat_signals: int = 0
    trades_requested: int = 0

    # Performance timing (added for unified statistics)
    decision_total_time_ms: float = 0.0
    decision_avg_time_ms: float = 0.0
    decision_min_time_ms: float = 0.0
    decision_max_time_ms: float = 0.0

    # Optional backtesting metadata for validation
    backtesting_metadata: Optional[BacktestingMetadata] = None


@dataclass
class WorkerCoordinatorPerformanceStats:
    """
    Performance statistics for WorkerOrchestrator.

    Tracks parallel execution and tick processing.
    Minimal structure - calculations done in reporting layer.

    Args:
        ticks_processed: Number of ticks processed
        parallel_time_saved_ms: Total time saved by parallel execution
    """
    parallel_workers: bool = False
    ticks_processed: int = 0
    parallel_time_saved_ms: float = 0.0
