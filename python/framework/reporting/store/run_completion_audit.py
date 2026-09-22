"""
FiniexTestingIDE - Run Completion Audit

Which runs started and never reached their close.

A run registers in the run index from its HEADER, written before anything can fail, and its
ledger row is the LAST step of the report coordinator at close. Between those two moments a
process can be killed, crash, or lose its machine — and the run then exists in one store and
not the other, with nothing anywhere saying so. Measured 2026-09-18 on a live session stopped
from the debugger: the index held it, the ledger did not, and the summary file was created and
never filled.

The derivation is a set difference and needs no new state.

TWO questions live here, and they are not each other's mirror image however much they look it.
`unfinished_runs` asks which run STARTED and never booked; `rows_with_missing_records` asks which
booking lost the run behind it. The second is deliberately NOT the plain reverse set difference:
ledger rows with no index entry at all are normal and numerous, because the ledger predates the
run index, so reporting that direction would bury the finding under ordinary history. It asks
instead about rows the index DOES know and whose directory is nevertheless gone — and it skips
the ones a prune stamped, because a deliberate deletion is accounted for and an unaccounted one
is the whole point.
"""

from pathlib import Path
from typing import Callable, Dict, List, Optional

from python.framework.types.api.report_types import ParentKind, RunInfo, RunResultRow


def unfinished_runs(
    runs: List[RunInfo],
    ledger_rows: List[RunResultRow],
    parent_id: Optional[str] = None,
    parent_kind: Optional[ParentKind] = None,
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
        parent_kind: Restrict further to parents of this kind. Both kinds of id are a timestamp
            plus a hash, so an id alone cannot say which it is — a caller asking a DEPLOYMENT
            question says so here rather than trusting the id to be unambiguous (#386). A row
            indexed before the discriminator existed carries no kind and is therefore NOT
            matched: an unknown kind is not a claim that it is this one

    Returns:
        The unfinished runs, oldest first
    """
    completed = {row.run_id for row in ledger_rows}
    selected = [r for r in runs if r.run_id not in completed]
    if parent_id is not None:
        selected = [r for r in selected if r.parent_id == parent_id]
    if parent_kind is not None:
        selected = [r for r in selected if r.parent_kind == parent_kind]
    return sorted(selected, key=lambda r: r.start_time)


def unfinished_by_group(
    runs: List[RunInfo],
    ledger_rows: List[RunResultRow],
    parent_id: Optional[str] = None,
    parent_kind: Optional[ParentKind] = None,
) -> Dict[str, List[RunInfo]]:
    """
    The same answer split by run group, because the two pipelines carry different consequence.

    An unfinished SIMULATION is a batch somebody stopped; an unfinished LIVE session traded
    real money and left no record of what it did.

    Args:
        runs: Every run the index holds
        ledger_rows: Every ledger row
        parent_id: Restrict to one parent (a deployment or a sweep), or None for all
        parent_kind: Restrict further to parents of this kind, or None for any

    Returns:
        Group name -> its unfinished runs, oldest first (groups with none are omitted)
    """
    grouped: Dict[str, List[RunInfo]] = {}
    for run in unfinished_runs(runs, ledger_rows, parent_id=parent_id,
                               parent_kind=parent_kind):
        grouped.setdefault(run.group, []).append(run)
    return grouped


def _directory_exists(run_dir: Optional[str]) -> bool:
    """
    Whether a run's directory is still on disk.

    Args:
        run_dir: The directory the index recorded for that run, or None when it recorded none

    Returns:
        True when it exists; False for an absent path, which is a run the index cannot locate
    """
    return bool(run_dir) and Path(run_dir).is_dir()


def rows_with_missing_records(
    ledger_rows: List[RunResultRow],
    run_dirs: Dict[str, Optional[str]],
    exists: Callable[[Optional[str]], bool] = _directory_exists,
) -> List[RunResultRow]:
    """
    Ledger rows whose records are gone without anyone having said so.

    The mirror of `unfinished_runs`, and it answers the opposite question: that one finds a run
    that started and never booked, this one finds a booking whose run is no longer there. Both
    are set differences over the same two stores (§44), and neither needs new state.

    Two conditions, and the second is what keeps this from firing on every ordinary prune:

      - the run index KNOWS this run, so we know where its directory should be. A ledger row the
        index never saw is normal and numerous — the ledger predates the run index — and
        reporting that direction would bury the finding under history.
      - the row carries NO `records_pruned_at`. A prune stamps the rows it orphans, so a
        deliberate deletion is accounted for. What is left is an absence nobody recorded: a
        directory removed by hand, a restored backup, a half-finished move.

    Every matching row is returned rather than one per run: a run books one row per account
    currency, and collapsing them here would be the same quiet loss this project has already
    measured in the deployment history. The caller decides how to phrase the count.

    The directories arrive as DATA rather than being read off the run rows: `RunInfo` is an API
    model and carries no filesystem path, which is right — a local path is not something an HTTP
    consumer should be handed. `RunIndex.run_dirs_of` resolves them in one read (0.003 s against
    0.88 s for the per-run form over 118 runs), and the caller passes the mapping in.

    Args:
        ledger_rows: Every ledger row
        run_dirs: run_id → its recorded directory, for every run the index holds. None where the
            index carries no path for a run, which counts as missing like any other absence
        exists: Whether a recorded directory is there — injected so the derivation stays testable

    Returns:
        The rows whose records vanished unaccounted for, oldest run first
    """
    missing = {run_id for run_id, run_dir in run_dirs.items() if not exists(run_dir)}
    if not missing:
        return []
    return sorted(
        (row for row in ledger_rows
         if row.run_id in missing and not row.records_pruned_at),
        key=lambda row: row.run_timestamp)
