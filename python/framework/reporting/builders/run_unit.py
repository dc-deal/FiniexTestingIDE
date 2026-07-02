"""
Run unit (#391 Phase 2) — the unified per-unit report source.

A run is a list of RunUnits: sim = N scenarios, live = 1 session. Each unit carries
its identity (name, symbol) plus the per-unit raw sources every section postprocessor
reads. Building the units once — with the symbol resolved from the index-synced
scenario for sim (ProcessResult carries none) — removes the per-section batch/session
extraction that each builder used to repeat. Every builder then maps ONE unit and
never re-iterates the run.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats, WorkerCoordinatorPerformanceStats, WorkerPerformanceStats)
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.types.trading_env_types.pending_order_stats_types import PendingOrderStats
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats


@dataclass
class RunUnit:
    """One run unit's report source (sim: a scenario; live: the session)."""
    name: str
    symbol: str
    data_source: str = ''           # data broker type (sim: scenario; live: '')
    sentiment_source: str = ''      # sentiment feed label (#429 sim scenario / #431 live profile; '' if none)
    has_error: bool = False         # hybrid: partial data + error (sim) / emergency (live)
    trade_history: List[TradeRecord] = field(default_factory=list)
    order_history: List[OrderResult] = field(default_factory=list)
    portfolio_stats: Optional[PortfolioStats] = None
    execution_stats: Optional[ExecutionStats] = None
    pending_stats: Optional[PendingOrderStats] = None
    # Worker / decision performance (unified — both pipelines; #398). Coordination
    # is sim-only (the live session has no worker coordinator) → Optional, None on live.
    worker_statistics: List[WorkerPerformanceStats] = field(default_factory=list)
    decision_statistics: Optional[DecisionLogicStats] = None
    coordination_statistics: Optional[WorkerCoordinatorPerformanceStats] = None


def run_units_from_batch(batch: BatchExecutionSummary) -> List[RunUnit]:
    """
    Extract the run units from a sim batch.

    Args:
        batch: The completed batch summary (scenarios = units)

    Returns:
        One RunUnit per scenario (symbol resolved via the index-synced SingleScenario);
        scenarios without a tick-loop result are skipped
    """
    units: List[RunUnit] = []
    for result in batch.process_result_list:
        tick_loop = getattr(result, 'tick_loop_results', None)
        if tick_loop is None:
            continue
        scenario = batch.get_scenario_by_process_result(result)
        units.append(RunUnit(
            name=result.scenario_name,
            symbol=scenario.symbol,
            data_source=scenario.data_broker_type,
            sentiment_source=scenario.data_sentiment_type,
            has_error=bool(result.error_type or result.error_message),
            trade_history=tick_loop.trade_history or [],
            order_history=tick_loop.order_history or [],
            portfolio_stats=tick_loop.portfolio_stats,
            execution_stats=tick_loop.execution_stats,
            pending_stats=tick_loop.pending_stats,
            worker_statistics=tick_loop.worker_statistics or [],
            decision_statistics=tick_loop.decision_statistics,
            coordination_statistics=tick_loop.coordination_statistics,
        ))
    return units


def run_units_from_session(
    session: AutoTraderResult, name: str, symbol: str,
    sentiment_source: str = '') -> List[RunUnit]:
    """
    The single run unit of a live session.

    Args:
        session: The collected session result
        name: Unit label (profile name / symbol)
        symbol: Traded symbol
        sentiment_source: The session's sentiment feed label (#431; '' if none)

    Returns:
        A one-element list with the session's RunUnit
    """
    return [RunUnit(
        name=name,
        symbol=symbol,
        sentiment_source=sentiment_source,
        has_error=session.emergency_reason is not None,
        trade_history=session.trade_history or [],
        order_history=session.order_history or [],
        portfolio_stats=session.portfolio_stats,
        execution_stats=session.execution_stats,
        worker_statistics=session.worker_statistics or [],
        decision_statistics=session.decision_statistics,
    )]
