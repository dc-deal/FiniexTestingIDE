"""
FiniexTestingIDE - Algo Heartbeat Block

The loop core's step that computes the decision between ticks, the ghost pass (#360, #458).
Moved unchanged from the tick pipeline core both runners shared since #303.
"""

from typing import Optional

from python.framework.types.decision_logic_types import Decision
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


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
