"""
FiniexTestingIDE - Shared Tick Pipeline Core (#303)

The algo pipeline steps both tick loops (simulation process_tick_loop,
AutoTrader autotrader_tick_loop) execute identically — extracted to ONE
implementation so ordering divergence between the two loops is impossible
by construction (the #293 bug class: bar rendering vs clipping gate order).

Runner-specific concerns stay in the runners by design:
    - clipping gate + per-step profiling (simulation)
    - safety override, display stats, clipping monitor (AutoTrader)
    - step 5 execute_decision + its error handling (diverges per runner)
    - the #348 decision-event drain boundary
    - clock injection (simulated time vs wall-clock, #360)
"""

from typing import Dict, Optional

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.types.decision_logic_types import Decision
from python.framework.types.market_types.market_data_types import TickData
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def render_bars_for_tick(tick: TickData, bar_controller: BarRenderingController) -> Dict:
    """
    Step 2 — render bars for EVERY tick, before any gate.

    Bars must reflect the complete market data stream — including ticks the
    algo path skips. Clipping simulates "algo was too slow to react", NOT
    "market data was incomplete", so this must run before the simulation
    clipping gate. Same ordering in both pipelines.

    Args:
        tick: Current tick
        bar_controller: Bar rendering controller

    Returns:
        current_bars dict from bar_controller.process_tick
    """
    return bar_controller.process_tick(tick)


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


def run_ghost_pass(worker_orchestrator: WorkerOrchestrator) -> Optional[Decision]:
    """
    Heartbeat ghost-pass core (#360) — decision compute between ticks.

    Workers do not recompute — the orchestrator serves their cached results;
    the decision runs with tick=None so an opt-in algo can advance internal
    state and issue follow-up orders. Never renders bars, never advances
    tick state. The surrounding cadence (clock injection, executor
    heartbeat, #208 gap gate, #348 drains, execute_decision) stays
    runner-specific.

    Args:
        worker_orchestrator: Orchestrator (cached worker results + decision)

    Returns:
        Decision to execute with tick=None, or None when the decision
        logic does not participate in heartbeats
    """
    return worker_orchestrator.process_heartbeat()
