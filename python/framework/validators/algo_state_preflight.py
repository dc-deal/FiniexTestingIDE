"""
FiniexTestingIDE - Algo State Pre-Flight Validation (#354)

A pre-run check that inspects the algo itself (not its configuration): is the
state snapshot it would persist actually JSON-serializable? This is the first
member of the algo pre-flight check family (siblings: #249 perf/output cert,
#359 algo-clock guard) — distinct from config validation, run from a single thin
call-site in each pipeline (Simulation Phase 0 + AutoTrader startup).

The backtest is the development loop, so running this in Simulation catches a
non-serializable snapshot before the algo ever goes live.
"""

import json

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.exceptions.persistence_errors import StatePersistenceError


def validate_state_snapshot_serializable(decision_logic: AbstractDecisionLogic) -> None:
    """
    Assert the algo's state snapshot is JSON-serializable. No-op unless the algo
    opts into persistence (uses_state_persistence()).

    Probes get_state_snapshot() once; on failure it pins down the offending
    top-level key(s) and type(s) for a precise message instead of a bare TypeError.

    Args:
        decision_logic: The decision logic to pre-flight

    Returns:
        None — raises StatePersistenceError if the snapshot is not serializable
    """
    if not decision_logic.uses_state_persistence():
        return

    snapshot = decision_logic.get_state_snapshot()
    try:
        json.dumps(snapshot)
        return
    except TypeError:
        pass  # serializable as a whole failed → locate the offender(s) below

    offenders = []
    if isinstance(snapshot, dict):
        for key, value in snapshot.items():
            try:
                json.dumps(value)
            except TypeError:
                offenders.append(f"'{key}' ({type(value).__name__})")
    detail = ', '.join(offenders) if offenders else f'snapshot type {type(snapshot).__name__}'

    raise StatePersistenceError(
        f"Decision logic '{decision_logic.name}' get_state_snapshot() is not "
        f"JSON-serializable: {detail}. Use only JSON primitives "
        f"(str/int/float/bool/list/dict/None); store timestamps as ISO strings."
    )
