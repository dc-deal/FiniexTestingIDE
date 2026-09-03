"""
FiniexTestingIDE - AutoTrader Pipeline Bundle

What `setup_pipeline` hands back to a live session: the wired objects the tick loop runs on.

A bundle rather than a tuple, and the reason is the failure it prevents: seven returns in a
fixed order met seven targets in a fixed order, so two neighbours of related type could be
swapped at the call site and nothing — not the type checker, not a test — would notice. A
field name is checked by the reader and by the tools.

Lives beside its builder rather than in `framework/types/` (§6): every field is a live
collaborator, and the types module cannot import the units that build them without a cycle.
Its simulation counterpart is `process/process_pipeline_bundle.py`.
"""

from dataclasses import dataclass

from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


@dataclass
class AutotraderPipelineBundle:
    """
    The live pipeline as one object.

    Args:
        executor: The live trade executor, broker config and trading context already wired
        bar_controller: Bar rendering for the timeframes the workers asked for
        worker_orchestrator: The workers plus the DecisionTradingApi wiring
        decision_logic: The strategy, parameters validated and hooks checked
        clipping_monitor: Tick-clipping observation for the session
        trading_model: SPOT or MARGIN, resolved from the broker config
        display_label_cache: Pre-resolved labels, so the display renders without lookups
    """
    executor: AbstractTradeExecutor
    bar_controller: BarRenderingController
    worker_orchestrator: WorkerOrchestrator
    decision_logic: AbstractDecisionLogic
    clipping_monitor: LiveClippingMonitor
    trading_model: TradingModel
    display_label_cache: DisplayLabelCache
