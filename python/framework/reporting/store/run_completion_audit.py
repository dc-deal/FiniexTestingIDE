"""
FiniexTestingIDE - Run Completion Audit

Which runs started and never reached their close.

A run registers in the run index from its HEADER, written before anything can fail, and its
ledger row is the LAST step of the report coordinator at close. Between those two moments a
process can be killed, crash, or lose its machine — and the run then exists in one store and
not the other, with nothing anywhere saying so. Measured 2026-09-18 on a live session stopped
from the debugger: the index held it, the ledger did not, and the summary file was created and
never filled.

The derivation is a set difference and needs no new state. It is deliberately ONE-DIRECTIONAL:
ledger rows with no index entry are normal and numerous, because the ledger predates the run
index, so that direction says nothing about anything.
"""

from typing import Dict, List, Optional

from python.framework.types.api.report_types import RunInfo, RunResultRow


def unfinished_runs(
    runs: List[RunInfo],
    ledger_rows: List[RunResultRow],
    parent_id: Optional[str] = None,
) -> List[RunInfo]:
    """
    Runs the index registered at start that the ledger never received at close.

    A session running RIGHT NOW is indistinguishable from one that died — both have a header
    and no ledger row — so the answer is stated as "not completed" and never as "aborted".
    Whoever reads it knows whether a bot is up.

    Args:
        runs: Every run the index holds
        ledger_rows: Every ledger row
        parent_id: Restrict to the runs belonging to this parent (a deployment or a sweep),
            or None for all of them

    Returns:
        The unfinished runs, oldest first
    """
    completed = {row.run_id for row in ledger_rows}
    selected = [r for r in runs if r.run_id not in completed]
    if parent_id is not None:
        selected = [r for r in selected if r.parent_id == parent_id]
    return sorted(selected, key=lambda r: r.start_time)


def unfinished_by_group(
    runs: List[RunInfo],
    ledger_rows: List[RunResultRow],
    parent_id: Optional[str] = None,
) -> Dict[str, List[RunInfo]]:
    """
    The same answer split by run group, because the two pipelines carry different consequence.

    An unfinished SIMULATION is a batch somebody stopped; an unfinished LIVE session traded
    real money and left no record of what it did.

    Args:
        runs: Every run the index holds
        ledger_rows: Every ledger row
        parent_id: Restrict to one parent (a deployment or a sweep), or None for all

    Returns:
        Group name -> its unfinished runs, oldest first (groups with none are omitted)
    """
    grouped: Dict[str, List[RunInfo]] = {}
    for run in unfinished_runs(runs, ledger_rows, parent_id=parent_id):
        grouped.setdefault(run.group, []).append(run)
    return grouped
