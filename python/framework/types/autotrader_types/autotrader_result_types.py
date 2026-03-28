"""
FiniexTestingIDE - AutoTrader Result Types
Result data structures for live AutoTrader sessions.
"""

from dataclasses import dataclass, field
from typing import List

from python.framework.types.autotrader_types.clipping_monitor_types import ClippingSessionSummary
from python.framework.types.performance_types.performance_stats_types import DecisionLogicStats, WorkerPerformanceStats
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats


@dataclass
class AutoTraderResult:
    """
    Complete result of an AutoTrader live session.

    Collected after shutdown (normal or emergency).

    Args:
        session_duration_s: Total session duration in seconds
        ticks_processed: Total ticks processed
        ticks_clipped: Total ticks that experienced clipping
        portfolio_stats: Portfolio performance statistics
        execution_stats: Order execution statistics
        trade_history: Completed trade records
        order_history: All order results
        clipping_summary: Clipping monitor session summary
        decision_statistics: Decision logic execution stats
        worker_statistics: Per-worker performance stats
        shutdown_mode: How the session ended ('normal' or 'emergency')
        warning_count: Total warnings logged during session
        error_count: Total errors logged during session
    """
    session_duration_s: float = 0.0
    ticks_processed: int = 0
    ticks_clipped: int = 0
    portfolio_stats: PortfolioStats = None
    execution_stats: ExecutionStats = None
    trade_history: List[TradeRecord] = field(default_factory=list)
    order_history: List[OrderResult] = field(default_factory=list)
    clipping_summary: ClippingSessionSummary = field(default_factory=ClippingSessionSummary)
    decision_statistics: DecisionLogicStats = None
    worker_statistics: List[WorkerPerformanceStats] = field(default_factory=list)
    shutdown_mode: str = 'normal'
    warning_count: int = 0
    error_count: int = 0
