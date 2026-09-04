"""
FiniexTestingIDE - Process Pipeline Bundle

What `process_startup_preparation` hands back to a scenario subprocess: the wired objects the
tick loop runs on, all of them created inside the subprocess.

A bundle rather than a tuple, for the same reason as its live counterpart
(`autotrader/autotrader_pipeline_bundle.py`): returns in a fixed order met targets in a fixed
order, and two neighbours of related type could be swapped at the call site without anything
noticing. The scenario logger is no longer part of it — the caller passes it in and it comes
back unchanged, so returning it was a second name for something already held.

Lives beside its builder rather than in `framework/types/` (§6): every field is a live
collaborator, and the types module cannot import the units that build them without a cycle.
"""

from dataclasses import dataclass
from typing import Tuple

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.market_types.market_data_types import TickData
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


@dataclass
class ProcessPipelineBundle:
    """
    One scenario's pipeline as one object.

    Args:
        worker_coordinator: The workers plus the DecisionTradingApi wiring
        trade_simulator: The scenario's trade executor, broker config and context wired
        bar_rendering_controller: Bar rendering for the timeframes the workers asked for
        decision_logic: The strategy, parameters validated and hooks checked
        ticks: The scenario's deserialized tick series, in order
    """
    worker_coordinator: WorkerOrchestrator
    trade_simulator: AbstractTradeExecutor
    bar_rendering_controller: BarRenderingController
    decision_logic: AbstractDecisionLogic
    ticks: Tuple[TickData, ...]
