"""
FiniexTestingIDE - Loop Context (#458)

A run's collaborators, bundled once for the loop core's passes.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.reporting.booking_period_recorder import BookingPeriodRecorder
from python.framework.reporting.builders.booking_period_builder import PeriodSnapshot
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


@dataclass(slots=True)
class LoopContext:
    """
    What a pass of the loop core works with, built once per scenario or session by its runner.

    A bundle of the run's live collaborators rather than data, so it lives beside the passes that
    read it rather than among the types. A pass reads its fields directly, with no getter calls,
    because it runs for every tick of a backtest.

    Args:
        executor: The trade executor — the clock, the broker path, the resolutions
        bar_controller: The bar rendering controller
        orchestrator: The worker orchestrator, which computes the workers and the decision
        decision_logic: The decision logic, which executes a decision
        decision_event_dispatcher: The decision event channel (#348), or None for a logic without
        symbol: The traded symbol, for the bar history
        booking: The run's booking period recorder, asked at every pass whether a trading day ended
        seal_source: What a seal reads at its instant — the trade records and the stock reading
        reads_closed_bars: Whether the run records state frames, the only reader of the bars a
            tick closed
        profiling_enabled: Whether the backtest times its steps; False live
        profile_times: The backtest's step timings in milliseconds, by step; empty when off
        profile_counts: How often each timed step ran, by step; empty when off
    """
    executor: AbstractTradeExecutor
    bar_controller: BarRenderingController
    orchestrator: WorkerOrchestrator
    decision_logic: AbstractDecisionLogic
    decision_event_dispatcher: Optional[DecisionEventDispatcher]
    symbol: str
    booking: BookingPeriodRecorder
    seal_source: Callable[[], Tuple[List[TradeRecord], PeriodSnapshot]]
    reads_closed_bars: bool
    profiling_enabled: bool
    profile_times: Dict[str, float]
    profile_counts: Dict[str, int]
