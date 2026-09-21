"""
Ledger aggregation (#537) — the ABSCHLUSS step, and the first caller of `COLUMN_REDUCTION`.

A ledger row is one booking period of one unit. Every question above that level — what did this
session earn, what did this deployment do, how does this parameter combination rank — is asked
by combining rows, and the rule for combining them is already written down: `COLUMN_REDUCTION`
declares, per column, how it folds. Until this module nothing READ that declaration but a test,
which is the state §49 warns about: a declaration with no caller is a comment that decays.

**This is why no aggregate row is stored.** The precomputed total used to sit beside the periods
it summarised, and neither the deployment history (which SUMS rows) nor the sweep ranking (which
SORTS them) could tell a summary from its own evidence. Derived here instead, the total is
recomputed on demand and cannot drift from the rows it comes from — the same argument that makes
a Hauptbuch trustworthy in the first place (§48).

**A DERIVE column is not folded, it is REBUILT**, and each one needs its own domain knowledge:
a win rate comes from the summed counts, a profit factor from the summed components, a mean from
its own weight. Two are honestly unrecoverable here and answer None — the streaks, because a run
of winners can CROSS a period boundary and no arithmetic over two summaries can see that.
"""

from typing import Any, Dict, List, Optional, Sequence

from python.framework.reporting.store.run_results_ledger import (
    COLUMN_COMPANION_OF,
    COLUMN_REDUCTION,
    LEDGER_COLUMNS,
)
from python.framework.types.api.report_types import RunResultRow
from python.framework.types.run_results_types import Reduction

# How a row is ordered when a column asks for "the most recent". The first stamp a row actually
# carries wins: a booking period is closed at `segment_closed_at`, a row that books no period was
# written at `recorded_at_utc`, and everything older than that column has only its run timestamp.
_RECENCY_KEYS = ('segment_closed_at', 'recorded_at_utc', 'run_timestamp')


def aggregate_ledger_rows(
    rows: List[RunResultRow],
    by: Sequence[str] = ('run_id', 'currency'),
) -> List[RunResultRow]:
    """
    Combine ledger rows into one row per group, following each column's declared reduction.

    Args:
        rows: The rows to combine
        by: The grouping key — `(run_id, currency)` gives one row per run, which is what a
            ranking reads; `(deployment_id, currency)` gives one per deployment

    Returns:
        One row per group, ordered by the group key. An empty input yields an empty list
    """
    groups: Dict[tuple, List[RunResultRow]] = {}
    for row in rows:
        groups.setdefault(tuple(getattr(row, key, '') for key in by), []).append(row)
    return [_combine(groups[key]) for key in sorted(groups, key=lambda k: tuple(map(str, k)))]


def identity_conflicts(rows: List[RunResultRow]) -> Dict[str, List[Any]]:
    """
    The IDENTITY columns whose values do NOT agree across these rows.

    `COLUMN_REDUCTION` declares twenty-eight columns as "must agree across the rows, or they
    were never comparable", and until this function nothing checked it. That mattered little
    while the map was read by nobody; it matters now, because `aggregate_ledger_rows` RELIES on
    the claim — it keeps one value and discards the rest.

    Reported rather than raised: a disagreement is a statement about the DATA, and a report that
    refused to render it would hide exactly the case a reader needs to see (§33, and the same
    shape as the mixed-logic-version advisory).

    Args:
        rows: The rows a caller is about to combine

    Returns:
        Column → the distinct values found, for every IDENTITY column with more than one
    """
    conflicts: Dict[str, List[Any]] = {}
    for column, reduction in COLUMN_REDUCTION.items():
        if reduction is not Reduction.IDENTITY:
            continue
        # Compared by their STRING form, because two IDENTITY columns parse back into dicts
        # (`worker_versions`, `sweep_params`) and a dict cannot go in a set. The string form is
        # what the comparison needs anyway — the question is whether the rows agree, not what
        # the value is.
        seen: Dict[str, Any] = {}
        for row in rows:
            value = _get(row, column)
            if value is None or value == '':
                continue
            seen.setdefault(str(value), value)
        if len(seen) > 1:
            conflicts[column] = [seen[key] for key in sorted(seen)]
    return conflicts


def _combine(rows: List[RunResultRow]) -> RunResultRow:
    """
    One group of rows as a single row.

    Args:
        rows: The group, at least one row

    Returns:
        The combined row
    """
    ordered = sorted(rows, key=_recency)
    combined: Dict[str, Any] = {}
    for column in LEDGER_COLUMNS:
        reduction = COLUMN_REDUCTION[column]
        if reduction is Reduction.DERIVE or reduction is Reduction.COMPANION:
            continue                            # both need the folded values below
        combined[column] = _fold(column, reduction, rows, ordered)
    _apply_companions(combined, rows)
    _apply_derived(combined, rows)
    return RunResultRow(**{k: v for k, v in combined.items() if v is not None})


def _fold(
    column: str,
    reduction: Reduction,
    rows: List[RunResultRow],
    ordered: List[RunResultRow],
) -> Any:
    """
    Apply one column's declared reduction.

    Args:
        column: The column
        reduction: Its declared class
        rows: The group
        ordered: The same group, oldest first

    Returns:
        The combined value, or None when nothing was measured
    """
    values = [_get(row, column) for row in rows]
    present = [v for v in values if v is not None and v != '']

    if reduction is Reduction.SUM:
        return sum(present) if present else None
    if reduction is Reduction.MAX:
        # By MAGNITUDE, and the sign is kept: a drawdown is recorded negative while an
        # excursion or a peak is positive, so "the largest" has to mean the same thing for both.
        return max(present, key=abs) if present else None
    if reduction is Reduction.MIN:
        return min(present) if present else None
    if reduction is Reduction.LAST:
        return _last_present(ordered, column)
    if reduction is Reduction.IDENTITY:
        return present[0] if present else None
    if reduction is Reduction.UNION:
        # Type-preserving on purpose. A UNION column is a comma-joined set IN THE LEDGER, but
        # the typed row parses some of them back into real structures — `symbols` is a list —
        # and handing a joined string to a list field is a validation error rather than a
        # value. The set is the same either way; only its packaging differs.
        parts = {
            item
            for value in present
            for item in (value if isinstance(value, list)
                         else [p.strip() for p in str(value).split(',') if p.strip()])
        }
        if any(isinstance(value, list) for value in present):
            return sorted(parts)
        return ','.join(sorted(parts))
    if reduction is Reduction.SPAN_START:
        return min(present) if present else None
    if reduction is Reduction.SPAN_END:
        return max(present) if present else None
    return None


def _apply_companions(combined: Dict[str, Any], rows: List[RunResultRow]) -> None:
    """
    Take each companion from the row that WON its leader, never by its own extremum.

    The drawdown trio is the case: whichever period owns the deepest decline also supplies the
    peak it fell from and the share it was. Reduced separately they would pair one period's
    trough with another's peak — the defect #497 removed one layer down, and it would be back
    here the moment a caller folded them independently.

    Args:
        combined: The row being built, modified in place
        rows: The group
    """
    for companion, leader in COLUMN_COMPANION_OF.items():
        winner = combined.get(leader)
        if winner is None:
            combined[companion] = None
            continue
        source = next((row for row in rows if _get(row, leader) == winner), None)
        combined[companion] = _get(source, companion) if source is not None else None


def _apply_derived(combined: Dict[str, Any], rows: List[RunResultRow]) -> None:
    """
    Rebuild every DERIVE column from the folded components — never from the rows' own values.

    A rate cannot be folded out of two periods; its COMPONENTS can be added on any level, which
    is the whole reason the ledger carries `gross_profit` / `gross_loss` beside the profit factor
    and the trade counts beside the win rate.

    Two columns answer None on purpose. `max_consecutive_wins` and `max_consecutive_losses`
    describe a SEQUENCE, and a run of winners can cross a period boundary: 2 and 3 in adjacent
    periods can be a run of 5, so no arithmetic over two summaries can recover it. It is
    re-derived over the records of the wider window or it is not answered.

    Args:
        combined: The row being built, modified in place
        rows: The group, for the weights a mean needs
    """
    total = combined.get('total_trades') or 0
    winners = combined.get('winning_trades') or 0
    gross_profit = combined.get('gross_profit') or 0.0
    gross_loss = combined.get('gross_loss') or 0.0

    combined['win_rate'] = (winners / total) if total else 0.0
    if not total:
        combined['profit_factor'] = None
    elif gross_loss > 0:
        combined['profit_factor'] = gross_profit / gross_loss
    else:
        combined['profit_factor'] = 0.0 if gross_profit == 0 else None

    # A mean is rebuilt as a WEIGHTED mean over the population each row measured — an unweighted
    # average of averages would let a period with two trades count as much as one with two
    # hundred.
    for column, weight in _WEIGHTED_MEANS.items():
        combined[column] = _weighted_mean(rows, column, weight)

    # The run's weakest channel, by its own definition (#433): a mean would hide a dead feed
    # behind a healthy one.
    ratios = [_get(row, 'signal_fresh_ratio') for row in rows]
    present = [r for r in ratios if r is not None]
    combined['signal_fresh_ratio'] = min(present) if present else None

    combined['max_consecutive_wins'] = None
    combined['max_consecutive_losses'] = None


# Which count weights each mean. The weight is the population that mean was taken over, so the
# combined figure is the mean the same records would have produced in one pass.
_WEIGHTED_MEANS: Dict[str, str] = {
    'expectancy': 'r_trade_count',
    'avg_win_r': 'r_win_count',
    'avg_loss_r': 'r_loss_count',
    'avg_mae_winners': 'winning_trades',
    'avg_mae_losers': 'losing_trades',
    'avg_mfe_losers': 'losing_trades',
    'avg_trade_duration_s': 'total_trades',
}


def _weighted_mean(rows: List[RunResultRow], column: str, weight_column: str) -> Optional[float]:
    """
    The mean of a per-row mean, weighted by the population it was measured over.

    Args:
        rows: The group
        column: The mean to rebuild
        weight_column: The count that mean was taken over

    Returns:
        The weighted mean, or None when nothing was measured
    """
    total_weight = 0.0
    total_value = 0.0
    for row in rows:
        value = _get(row, column)
        weight = _get(row, weight_column) or 0
        if value is None or weight <= 0:
            continue
        total_value += value * weight
        total_weight += weight
    return (total_value / total_weight) if total_weight else None


def _last_present(ordered: List[RunResultRow], column: str) -> Any:
    """
    The newest row's value for this column, skipping rows that never measured it.

    Args:
        ordered: The group, oldest first
        column: The column

    Returns:
        The value, or None
    """
    for row in reversed(ordered):
        value = _get(row, column)
        if value is not None and value != '':
            return value
    return None


def _recency(row: RunResultRow) -> str:
    """
    The stamp that orders a row against its siblings.

    Args:
        row: The row

    Returns:
        The first recency stamp it carries, or '' when it carries none
    """
    for key in _RECENCY_KEYS:
        value = _get(row, key)
        if value:
            return str(value)
    return ''


def _get(row: Optional[RunResultRow], column: str) -> Any:
    """
    One column's value, tolerating a column the model spells differently.

    `account_max_drawdown_pct` is the one case: the ledger column and the typed field agree, but
    reading through `getattr` for every column keeps this the only place that would have to know
    if a second one ever diverged.

    Args:
        row: The row, or None
        column: The ledger column

    Returns:
        The value, or None
    """
    if row is None:
        return None
    return getattr(row, column, None)


# Kept for callers that want the grouping without the combining — the deployment view needs the
# groups themselves to render one table per currency.
def group_ledger_rows(
    rows: List[RunResultRow],
    by: Sequence[str],
) -> Dict[tuple, List[RunResultRow]]:
    """
    The rows of each group, ungrouped by nothing else.

    Args:
        rows: The rows
        by: The grouping key

    Returns:
        Group key → its rows, in input order
    """
    groups: Dict[tuple, List[RunResultRow]] = {}
    for row in rows:
        groups.setdefault(tuple(getattr(row, key, '') for key in by), []).append(row)
    return groups
