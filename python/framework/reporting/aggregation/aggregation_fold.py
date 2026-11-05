"""
The aggregation fold (#537, #557) — the CLOSING step, and the caller of the figure catalog.

A ledger row is one booking period of one unit. Every question above that level — what did this
session earn, what did this deployment do, how does this parameter combination rank — is asked
by combining rows, and the rule for combining them is written down: the figure catalog declares,
per column, how it folds. A declaration with no caller is a comment that decays (§49); this module
is the caller.

**This is why no aggregate row is stored.** The precomputed total used to sit beside the periods
it summarised, and neither the deployment history (which SUMS rows) nor the sweep ranking (which
SORTS them) could tell a summary from its own evidence. Derived here instead, the total is
recomputed on demand and cannot drift from the rows it comes from — the same argument that makes
a ledger trustworthy in the first place (§48).

**The fold is a table, not a chain of branches.** Each reduction maps to its function, and each
DERIVE figure to its derivation or its weight. A chain of `if` branches ended in `return None`, so a
member with no branch emptied its column without a symptom; a table answers a missing member with
an error, and a test holds the table to the enum and to the catalog.

**A DERIVE column is not folded, it is REBUILT**, and each one needs its own domain knowledge:
a win rate comes from the summed counts, a profit factor from the summed components, a mean from
its own weight. Two are honestly unrecoverable here and answer None — the streaks, because a run
of winners can CROSS a period boundary and no arithmetic over two summaries can see that.
"""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, get_args

from python.framework.reporting.aggregation.figure_catalog import (
    COLUMN_COMPANION_OF,
    COLUMN_REDUCTION,
    COLUMN_WEIGHTED_BY,
    LEDGER_FIGURES,
)
from python.framework.types.aggregation_types import FoldInput, Reduction
from python.framework.types.api.report_types import RunResultRow

# How a row is ordered when a column asks for "the most recent". The first stamp a row actually
# carries wins: a booking period is closed at `period_closed_at`, a row that books no period was
# written at `recorded_at_utc`, and everything older than that column has only its run timestamp.
_RECENCY_KEYS = ('period_closed_at', 'recorded_at_utc', 'run_timestamp')

# The row fields that may be empty — derived from the model, so a field widened later is covered.
_NULLABLE_FIELDS = frozenset(
    name for name, field in RunResultRow.model_fields.items()
    if type(None) in get_args(field.annotation))


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

    The catalog declares its IDENTITY columns as "must agree across the rows, or they were never
    comparable", and until this function nothing checked it. That mattered little while the map
    was read by nobody; it matters now, because `aggregate_ledger_rows` RELIES on the claim — it
    keeps one value and discards the rest.

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
        # Compared by their STRING form, because some IDENTITY columns parse back into structures
        # (`worker_versions` is one) and a structure cannot go in a set. The string form is
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
    # The rows' positions, oldest first — the same for every column of the group.
    oldest_order = sorted(range(len(rows)), key=lambda index: _recency(rows[index]))
    # One account per unit: a backtest's scenarios each trade their own balance.
    one_account = len({row.unit_name for row in rows}) <= 1
    combined: Dict[str, Any] = {}
    for column, fold in _FOLDED_ON_THEIR_OWN:
        values = FoldInput([_get(row, column) for row in rows], oldest_order, one_account)
        combined[column] = fold(values)
    # The DERIVE and COMPANION columns need the folded values above
    _apply_companions(combined, rows)
    _apply_derived(combined, rows)
    # None stays None where the model lets a field be empty: dropped, a figure with a number for
    # its default would come back as that number — the zero a run that measured nothing never
    # booked (#646). Dropped only where the model refuses it, so a description keeps its default.
    return RunResultRow(**{k: v for k, v in combined.items()
                           if v is not None or k in _NULLABLE_FIELDS})


def _fold_sum(values: FoldInput) -> Any:
    """
    A flow or a counter: the rows partition it.

    Args:
        values: The column's values over the group

    Returns:
        The sum, or None when no row measured it
    """
    present = values.present()
    return sum(present) if present else None


def _fold_max(values: FoldInput) -> Any:
    """
    A plain maximum — a peak, and the latest end of a span.

    Args:
        values: The column's values over the group

    Returns:
        The largest value, or None when no row measured it
    """
    present = values.present()
    return max(present) if present else None


def _fold_max_abs(values: FoldInput) -> Any:
    """
    The largest by MAGNITUDE, and the sign is kept: a decline may be stored either way round, and
    rows of both ages sit in one store, so "the largest" has to mean the same for both.

    Args:
        values: The column's values over the group

    Returns:
        The value of largest magnitude, or None when no row measured it
    """
    present = values.present()
    return max(present, key=abs) if present else None


def _fold_min(values: FoldInput) -> Any:
    """
    A plain minimum — a trough, and the earliest end of a span.

    Args:
        values: The column's values over the group

    Returns:
        The smallest value, or None when no row measured it
    """
    present = values.present()
    return min(present) if present else None


def _fold_last(values: FoldInput) -> Any:
    """
    The newest row's value, skipping rows that never measured it — of one account only.

    Args:
        values: The column's values over the group

    Returns:
        The value, or None over several accounts or when no row measured it
    """
    if not values.one_account:
        return None
    for value in reversed(values.oldest_first()):
        if value is not None and value != '':
            return value
    return None


def _fold_first(values: FoldInput) -> Any:
    """
    The OLDEST row's own value, and nothing later in its place: an opening is a fact about the
    first instant, so when the first row did not record it the group's opening is unknown — a later
    row's opening would be a different instant's.

    Args:
        values: The column's values over the group

    Returns:
        The value, or None over several accounts or when the oldest row did not record it
    """
    if not values.one_account or not values.values:
        return None
    value = values.values[values.oldest_order[0]]
    return None if value == '' else value


def _fold_identity(values: FoldInput) -> Any:
    """
    A value the rows must agree on — the first one carried. Whether they do is
    `identity_conflicts`' question.

    Args:
        values: The column's values over the group

    Returns:
        The first value carried, or None
    """
    present = values.present()
    return present[0] if present else None


def _fold_union(values: FoldInput) -> Any:
    """
    A set, combined by union and never by concatenation.

    Type-preserving on purpose. A UNION column is a comma-joined set IN THE LEDGER, but the typed
    row parses some of them back into real structures — `symbols` is a list — and handing a joined
    string to a list field is a validation error rather than a value. The set is the same either
    way; only its packaging differs.

    Args:
        values: The column's values over the group

    Returns:
        The sorted union, as a list where a row carried a list, else comma-joined
    """
    present = values.present()
    parts = {
        item
        for value in present
        for item in (value if isinstance(value, list)
                     else [p.strip() for p in str(value).split(',') if p.strip()])
    }
    if any(isinstance(value, list) for value in present):
        return sorted(parts)
    return ','.join(sorted(parts))


# Every reduction that folds on its own, mapped to its function. A test holds the keys to the
# members of `Reduction` that fold on their own, so a new member cannot arrive without a fold —
# and if one did, the lookup raises instead of emptying the column.
FOLD_OF_REDUCTION: Dict[Reduction, Callable[[FoldInput], Any]] = {
    Reduction.SUM: _fold_sum,
    Reduction.MAX: _fold_max,
    Reduction.MAX_ABS: _fold_max_abs,
    Reduction.MIN: _fold_min,
    Reduction.LAST: _fold_last,
    Reduction.FIRST: _fold_first,
    Reduction.IDENTITY: _fold_identity,
    Reduction.UNION: _fold_union,
    Reduction.SPAN_START: _fold_min,
    Reduction.SPAN_END: _fold_max,
}

# The columns folded from their own values, each with its fold — resolved once at import, so a
# reduction without a fold fails here rather than in the first group that meets it.
_FOLDED_ON_THEIR_OWN: Tuple[Tuple[str, Callable[[FoldInput], Any]], ...] = tuple(
    (column, FOLD_OF_REDUCTION[declaration.reduction])
    for column, declaration in LEDGER_FIGURES.items()
    if declaration.reduction.folds_on_its_own())


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
    and the trade counts beside the win rate. A mean declares the count it was taken over and is
    rebuilt as the mean weighted by it; every other DERIVE figure has its derivation in
    `DERIVATION_OF`.

    Args:
        combined: The row being built, modified in place
        rows: The group, for the weights a mean needs
    """
    for column, weight in COLUMN_WEIGHTED_BY.items():
        combined[column] = _weighted_mean(rows, column, weight)
    for column, derivation in DERIVATION_OF.items():
        combined[column] = derivation(combined, rows)


def _derive_win_rate(combined: Dict[str, Any], rows: List[RunResultRow]) -> Optional[float]:
    """
    The win rate from the summed counts.

    A group that measured nothing — the rows of runs that failed before they measured anything —
    has no win rate rather than one of zero (#646); a group without a trade keeps its 0.0.

    Args:
        combined: The row being built, its flows already folded
        rows: The group

    Returns:
        The winners' share of the trades, 0.0 without a trade, None when nothing was measured
    """
    if combined.get('total_trades') is None:
        return None
    total = combined.get('total_trades') or 0
    winners = combined.get('winning_trades') or 0
    return (winners / total) if total else 0.0


def _derive_profit_factor(combined: Dict[str, Any], rows: List[RunResultRow]) -> Optional[float]:
    """
    The profit factor from the summed gross halves.

    Args:
        combined: The row being built, its flows already folded
        rows: The group

    Returns:
        Gross profit over gross loss; None without a trade or without a loss beside a profit,
        0.0 when neither half moved
    """
    if not (combined.get('total_trades') or 0):
        return None
    gross_profit = combined.get('gross_profit') or 0.0
    gross_loss = combined.get('gross_loss') or 0.0
    if gross_loss > 0:
        return gross_profit / gross_loss
    return 0.0 if gross_profit == 0 else None


def _derive_weakest_signal_channel(
    combined: Dict[str, Any],
    rows: List[RunResultRow],
) -> Optional[float]:
    """
    The run's weakest channel, by its own definition (#433): a mean would hide a dead feed behind
    a healthy one.

    Args:
        combined: The row being built
        rows: The group

    Returns:
        The smallest fresh ratio any row carried, or None when no row carried one
    """
    ratios = [_get(row, 'signal_fresh_ratio') for row in rows]
    present = [r for r in ratios if r is not None]
    return min(present) if present else None


def _derive_nothing_for_a_sequence(
    combined: Dict[str, Any],
    rows: List[RunResultRow],
) -> None:
    """
    A streak describes a SEQUENCE, and a run of winners can cross a period boundary: 2 and 3 in
    adjacent periods can be a run of 5, so no arithmetic over two summaries can recover it. It is
    re-derived over the records of the wider window or it is not answered.

    Args:
        combined: The row being built
        rows: The group

    Returns:
        None, always
    """
    return None


# Every DERIVE figure that is no weighted mean, mapped to its derivation. A test holds this and the
# catalog's weighted means to the DERIVE figures, each exactly once, so a new rate cannot arrive
# without saying how it is rebuilt.
DERIVATION_OF: Dict[str, Callable[[Dict[str, Any], List[RunResultRow]], Any]] = {
    'win_rate': _derive_win_rate,
    'profit_factor': _derive_profit_factor,
    'signal_fresh_ratio': _derive_weakest_signal_channel,
    'max_consecutive_wins': _derive_nothing_for_a_sequence,
    'max_consecutive_losses': _derive_nothing_for_a_sequence,
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
