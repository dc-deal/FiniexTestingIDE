"""
FiniexTestingIDE - Run List Figures

What each run in the run list DID — its figures per account currency, how it ended and what its
channels held — joined from the run-results ledger in ONE read, so the list answers without a
request per run, which is the N+1 a consumer would otherwise have to pay.

Folded by the ledger's own declared reductions (`aggregate_ledger_rows`), the same fold the sweep
ranking and the deployment history read, so the three cannot disagree about a run.

Read and folded once per CHANGE of the ledger directory rather than once per request: measured
2026-09-29 on this tree, reading the ledger costs 200-600 ms and grows with every run, while the
directory's stamp is one call and a listing. A run appends a new fragment and a rewrite replaces
one by rename, so both move the stamp.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from python.framework.reporting.aggregation.aggregation_fold import (
    aggregate_ledger_rows,
    fold_rows,
)
from python.framework.reporting.aggregation.tables.run_list_figure_table import RUN_LIST_TABLE
from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
from python.framework.reporting.store.run_results_ledger import LEDGER_COLUMNS, RunResultsLedger
from python.framework.types.api.report_types import (
    SESSION_KEY,
    RunListFigures,
    RunResultRow,
)

# ledger directory → (the stamp it was read under, the figures per run_id)
_cache: Dict[Path, Tuple[tuple, Dict[str, RunListFigures]]] = {}


def get_run_list_figures(ledger_dir: Path) -> Dict[str, RunListFigures]:
    """
    What the ledger recorded for every run, folded per run and account currency.

    Args:
        ledger_dir: The run-results ledger directory

    Returns:
        run_id → its figures; a run the ledger holds nothing for is absent
    """
    stamp = _ledger_stamp(ledger_dir)
    if stamp is None:
        return {}
    cached = _cache.get(ledger_dir)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    # Stored under the stamp taken BEFORE the read: a read that rebuilds the index moves the
    # stamp, and a fragment appended during the read must not hide behind the new one.
    figures = fold_run_list_figures(RunResultsLedger(ledger_dir).read_rows())
    _cache[ledger_dir] = (stamp, figures)
    return figures


def _ledger_stamp(ledger_dir: Path) -> Optional[tuple]:
    """
    What changes whenever the ledger's fragments do.

    Args:
        ledger_dir: The run-results ledger directory

    Returns:
        The directory's modification time and its fragment names, or None when there is no ledger
    """
    if not ledger_dir.exists():
        return None
    names = tuple(path.name for path in RunLedgerIndex(ledger_dir, LEDGER_COLUMNS).fragments())
    return ledger_dir.stat().st_mtime_ns, names


def fold_run_list_figures(rows: List[RunResultRow]) -> Dict[str, RunListFigures]:
    """
    Fold ledger rows into one figures entry per run.

    Args:
        rows: Every ledger row, all booking periods of every run

    Returns:
        run_id → its figures
    """
    # Per run and currency first, by the ledger's own table; then per run, where a run-level
    # column is served only when every currency row agrees on it (the run list's fold table).
    per_currency = aggregate_ledger_rows(rows, by=SESSION_KEY)
    return {figures.pop('run_id'): RunListFigures(**figures)
            for figures in fold_rows(per_currency, RUN_LIST_TABLE)}
