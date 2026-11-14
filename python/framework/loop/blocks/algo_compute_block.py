"""
FiniexTestingIDE - Algo Compute Block

The loop core's step that computes the workers and the decision for a tick (#458). Moved
unchanged from the tick pipeline core both runners shared since #303.
"""

from typing import Dict

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.types.decision_logic_types import Decision
from python.framework.types.market_types.market_data_types import TickData
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def execute_algo_path(
    tick: TickData,
    current_bars: Dict,
    bar_controller: BarRenderingController,
    worker_orchestrator: WorkerOrchestrator,
    symbol: str,
) -> Decision:
    """
    Steps 3–4 — bar history retrieval + worker/decision compute.

    Called for non-clipped ticks only. Step 5 (execute_decision) stays
    runner-specific: the live loop applies its safety override between
    compute and execute and captures the order result for rejection
    tracking; the sim loop wraps execution in its own error handling.

    Args:
        tick: Current tick (non-clipped)
        current_bars: Bars from render_bars_for_tick (same tick)
        bar_controller: Bar rendering controller (history source)
        worker_orchestrator: Orchestrator (workers + decision compute)
        symbol: Trading symbol for bar history retrieval

    Returns:
        Decision from worker_orchestrator.process_tick
    """
    bar_history = bar_controller.get_all_bar_history(symbol=symbol)
    bar_render_state = bar_controller.consume_bar_render_state()
    return worker_orchestrator.process_tick(
        tick=tick,
        current_bars=current_bars,
        bar_history=bar_history,
        bar_render_state=bar_render_state,
    )
