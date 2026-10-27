"""
FiniexTestingIDE - Run Completion Audit

Which runs started and never reached their close.

A run registers in the run index from its HEADER, written before anything can fail, and its
ledger row is written at its close — the last step before its footprint (#621), which is the run's
own record of having closed and is read by `run_footprint`, not here. Between those two moments a
process can be killed, crash, or lose its machine — and the run then exists in one store and
not the other, with nothing anywhere saying so. Measured 2026-09-18 on a live-adapter session stopped
from the debugger: the index held it, the ledger did not, and the summary file was created and
never filled.

The derivation is a set difference and needs no new state.

**A row is not yet a close (#476).** A session writes its booking periods into the ledger as each
trading day seals, so a running session — or one that died on day 29 — has rows. What says a run
COMPLETED is the row its close wrote: the period its end sealed (`session_end`), or a row that books
no period at all, which is only ever written at the close. A row a trading day sealed (`anchor`)
says the run was alive then, nothing more.

TWO questions live here, and they are not each other's mirror image however much they look it.
`unfinished_runs` asks which run STARTED and never booked; `rows_with_missing_records` asks which
booking lost the run behind it. The second is deliberately NOT the plain reverse set difference:
ledger rows with no index entry at all are normal and numerous, because the ledger predates the
run index, so reporting that direction would bury the finding under ordinary history. It asks
instead about rows the index DOES know and whose directory is nevertheless gone — and it skips
the ones a prune stamped, because a deliberate deletion is accounted for and an unaccounted one
is the whole point.
"""

import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import ParentKind, RunInfo, RunResultRow
from python.framework.types.run_results_types import PeriodCloseReason


def closes_its_run(period_close_reason: Any) -> bool:
    """
    Whether a ledger row was written at its run's close (#476).

    The period a run's end sealed says `session_end`; a row that books no period — every row
    written before booking periods existed, and an error row — is only ever written at the close.
    A trading day's seal (`anchor`) and an operator's close are written while the run goes on.

    Args:
        period_close_reason: The row's `period_close_reason` cell — empty, None or NaN on a row
            that books no period

    Returns:
        True when the row is the run's own close
    """
    if period_close_reason is None or (
            isinstance(period_close_reason, float) and math.isnan(period_close_reason)):
        return True
    return period_close_reason in ('', PeriodCloseReason.SESSION_END.value)


def run_completed(ledger: RunResultsLedger, run_id: str) -> bool:
    """
    Whether one run booked its results — the ledger holds the row its close wrote.

    Written at the run's close, so a run that has one completed, and a run that has none either
    is still going or died — whatever rows its sealed trading days left. Whether the run then
    also CLOSED — wrote everything and its footprint last — is a question for `run_footprint`.
    The same rule `unfinished_runs` applies to every run at once, asked here of one.

    Args:
        ledger: The run ledger
        run_id: The run in question

    Returns:
        True when the ledger holds the row of that run's close
    """
    frame = ledger.read(run_id=run_id)
    if frame.empty:
        return False
    if 'period_close_reason' not in frame.columns:
        return True
    return any(closes_its_run(reason) for reason in frame['period_close_reason'])


def unfinished_runs(
    runs: List[RunInfo],
    ledger_rows: List[RunResultRow],
    parent_id: Optional[str] = None,
    parent_kind: Optional[ParentKind] = None,
) -> List[RunInfo]:
    """
    Runs the index registered at start that the ledger never received at close.

    A session running RIGHT NOW is indistinguishable from one that died — both have a header
    and no row of a close, whatever rows their sealed days left — so the answer is stated as
    "not completed" and never as "aborted". Whoever reads it knows whether a bot is up.

    Args:
        runs: Every run the index holds
        ledger_rows: Every ledger row
        parent_id: Restrict to the runs belonging to this parent (a deployment or a sweep),
            or None for all of them
        parent_kind: Restrict further to parents of this kind. A parent id is an opaque key:
            it reads as a kind word and a timestamp (`deploy_20261008_110304_a1b2c3d4`), but
            that is for a person reading a listing, and nothing may parse it — so a caller
            asking a DEPLOYMENT question says so here rather than reading the kind from the id
            (#386). A row indexed before the discriminator existed carries no kind and is
            therefore NOT matched: an unknown kind is not a claim that it is this one

    Returns:
        The unfinished runs, oldest first
    """
    completed = {row.run_id for row in ledger_rows if closes_its_run(row.period_close_reason)}
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

    An unfinished SIMULATION is a batch somebody stopped; an unfinished AUTOTRADER session may
    have traded real money and left no record of what it did.

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
