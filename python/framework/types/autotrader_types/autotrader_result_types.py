"""
FiniexTestingIDE - AutoTrader Result Types
Result data structures for live AutoTrader sessions.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from python.framework.types.autotrader_types.clipping_monitor_types import ClippingSessionSummary
from python.framework.types.autotrader_types.cold_start_types import (
    ColdStartSituation,
    ColdStartVerdict,
)
from python.framework.types.disturbance_episode_types import DisturbanceEpisode, MarketDataTickStats
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats,
    WorkerPerformanceStats,
)
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.signal_data_types import SignalResolutionStats
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats
from python.framework.types.validation_types import ValidationResult


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
        open_positions: Positions still OPEN when the session ended (#492). Not a failure:
            the policy may leave them standing, and the report shows them as open and
            valued instead of claiming an exit that never reached the venue
        order_history: All order results
        clipping_summary: Clipping monitor session summary
        decision_statistics: Decision logic execution stats
        worker_statistics: Per-worker performance stats
        signal_statistics: Per-SIGNAL-worker resolution counters (#433)
        disturbance_episodes: Observed outage spans of both staleness domains (#451)
        market_data_tick_stats: Market-data resolution counters (#451 Part 4)
        shutdown_mode: How the session ended ('normal' or 'emergency')
        operator_interrupted: Whether the operator requested the stop (SIGINT)
        session_logger_buffer: The session logger's records (level, times, scope, message).
            Mirrors ProcessResult.scenario_logger_buffer, so both pipelines hand the reporting
            stage the same shape and the level filter lives at DERIVE, not here
        emergency_reason: Fatal cause when shutdown_mode == 'emergency' (None otherwise)
        cold_start_situation: What the boot step found at the venue (#355 / #493). None for
            a simulation, a dry run and a Field Study — the three cases with nothing to find.
            Captured raw; the report model is derived from it
        cold_start_verdict: What the decision logic answered about that situation
        session_validation_result: Post-run advisory findings (Tier 1) — the live counterpart
            of BatchExecutionSummary.batch_validation_result
    """
    session_duration_s: float = 0.0
    ticks_processed: int = 0
    ticks_clipped: int = 0
    portfolio_stats: Optional[PortfolioStats] = None
    execution_stats: Optional[ExecutionStats] = None
    trade_history: List[TradeRecord] = field(default_factory=list)
    open_positions: List[Position] = field(default_factory=list)
    # 'orders/positions' — the policy this session actually ran under (#492). An operator
    # reading a summary must be able to tell a position left standing by decision from one
    # that went missing, and '' says the session never got as far as resolving it.
    session_end_policy: str = ''
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
    session_logger_buffer: List[LogRecord] = field(default_factory=list)
    cold_start_situation: Optional[ColdStartSituation] = None
    cold_start_verdict: Optional[ColdStartVerdict] = None
    session_validation_result: List[ValidationResult] = field(default_factory=list)

    def add_session_validation_result(self, result: ValidationResult) -> None:
        """
        Append a post-run validation result to the session's validation channel.

        Args:
            result: The validation result to record
        """
        self.session_validation_result.append(result)

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

        if self.count_logged(LogLevel.ERROR):
            return RunOutcome.FINISHED_WITH_ERRORS

        return RunOutcome.SUCCESS

    def count_logged(self, level: LogLevel) -> int:
        """
        How many session-logger records of one level the run produced.

        A query on the result, so the renderers that show the count do not each filter the
        buffer themselves (§391 — PRESENT renders, it does not compute).

        Args:
            level: The level to count

        Returns:
            The number of matching records
        """
        return sum(1 for record in self.session_logger_buffer if record.level == level)

    def get_exit_code(self) -> int:
        """
        Process exit code for this session outcome.

        Returns:
            The outcome's exit code, so a supervisor reads the run result
        """
        return self.get_outcome().get_exit_code()
