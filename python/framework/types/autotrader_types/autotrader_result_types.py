"""
FiniexTestingIDE - AutoTrader Result Types
Result data structures for live AutoTrader sessions.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from python.framework.types.autotrader_types.clipping_monitor_types import ClippingSessionSummary
from python.framework.types.disturbance_episode_types import DisturbanceEpisode, MarketDataTickStats
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats,
    WorkerPerformanceStats,
)
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.signal_data_types import SignalResolutionStats
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
        signal_statistics: Per-SIGNAL-worker resolution counters (#433)
        disturbance_episodes: Observed outage spans of both staleness domains (#451)
        market_data_tick_stats: Market-data resolution counters (#451 Part 4)
        shutdown_mode: How the session ended ('normal' or 'emergency')
        operator_interrupted: Whether the operator requested the stop (SIGINT)
        warning_messages: Warning messages from session logger buffer
        error_messages: Error messages from session logger buffer
        emergency_reason: Fatal cause when shutdown_mode == 'emergency' (None otherwise)
    """
    session_duration_s: float = 0.0
    ticks_processed: int = 0
    ticks_clipped: int = 0
    portfolio_stats: Optional[PortfolioStats] = None
    execution_stats: Optional[ExecutionStats] = None
    trade_history: List[TradeRecord] = field(default_factory=list)
    order_history: List[OrderResult] = field(default_factory=list)
    clipping_summary: ClippingSessionSummary = field(default_factory=ClippingSessionSummary)
    decision_statistics: Optional[DecisionLogicStats] = None
    worker_statistics: List[WorkerPerformanceStats] = field(default_factory=list)
    signal_statistics: List[SignalResolutionStats] = field(default_factory=list)
    disturbance_episodes: List[DisturbanceEpisode] = field(default_factory=list)
    market_data_tick_stats: Optional[MarketDataTickStats] = None
    shutdown_mode: str = 'normal'
    operator_interrupted: bool = False
    emergency_reason: Optional[str] = None
    warning_messages: List[str] = field(default_factory=list)
    error_messages: List[str] = field(default_factory=list)

    def get_outcome(self) -> RunOutcome:
        """
        Grade this session (#372).

        An emergency the operator did not initiate is a failed run — a startup abort, a
        tick-loop crash, or a safety escalation (#348). An operator Ctrl+C is not: it also
        arrives as 'emergency', and emergency_reason does not separate the two, which is
        why operator_interrupted is explicit. Logged errors re-grade an otherwise clean
        session, closing the §35 asymmetry.

        Returns:
            The classified session outcome
        """
        if self.shutdown_mode == 'emergency' and not self.operator_interrupted:
            return RunOutcome.FAILED

        if self.error_messages:
            return RunOutcome.FINISHED_WITH_ERRORS

        return RunOutcome.SUCCESS

    def get_exit_code(self) -> int:
        """
        Process exit code for this session outcome.

        Returns:
            The outcome's exit code, so a supervisor reads the run result
        """
        return self.get_outcome().get_exit_code()
