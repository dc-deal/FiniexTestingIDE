"""
The aggregation fold (#537, #557) — the CLOSING step, and the caller of the figure catalog.

A ledger row is one booking period of one unit. Every question above that level — what did this
session earn, what did this deployment do, how does this parameter combination rank — is asked
by combining rows, and the rule for combining them is written down: the figure catalog declares,
per column, how it folds. A declaration with no caller is a comment that decays (§49); this module
is the caller.

**Every table, not only the ledger.** A trade, a portfolio unit, a worker's timings — each kind of
row has its own fold table (`FoldTable`), and `fold_rows` applies any of them the same way. The
ledger is one table among them (`tables/ledger_figure_table.py`), and `aggregate_ledger_rows` its
caller.

**This is why no aggregate row is stored.** The precomputed total used to sit beside the periods
it summarised, and neither the deployment history (which SUMS rows) nor the sweep ranking (which
SORTS them) could tell a summary from its own evidence. Derived here instead, the total is
recomputed on demand and cannot drift from the rows it comes from — the same argument that makes
a ledger trustworthy in the first place (§48).

**The fold is a table, not a chain of branches.** Each reduction maps to its function, and each
DERIVE figure to its derivation or its weight. A chain of `if` branches ended in `return None`, so a
member with no branch emptied its column without a symptom; a table answers a missing member with
an error, and a test holds the table to the enum and to the catalog.
"""

import math
from operator import attrgetter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, get_args

from python.framework.reporting.aggregation.figure_catalog import COLUMN_REDUCTION
from python.framework.reporting.aggregation.tables.ledger_figure_table import LEDGER_TABLE
from python.framework.types.aggregation_types import (
    Addition,
    Derivation,
    FigureDeclaration,
    FigureSource,
    FoldInput,
    FoldPlan,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import RunResultRow

# The row fields that may be empty — derived from the model, so a field widened later is covered.
_NULLABLE_FIELDS = frozenset(
    name for name, field in RunResultRow.model_fields.items()
    if type(None) in get_args(field.annotation))


def fold_rows(
    rows: Sequence[Any],
    table: FoldTable,
    by: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Combine rows into one set of figures per group, following each figure's declaration.

    Args:
        rows: The rows to combine, read by attribute
        table: The fold table of their kind
        by: The row fields that form the group key — the table's grain when omitted; empty folds
            every row into one group

    Returns:
        One mapping of figure to value per group, in the table's group order. An empty input
        yields an empty list
    """
    return [figures for _, figures in fold_keyed(rows, table, by)]


def fold_keyed(
    rows: Sequence[Any],
    table: FoldTable,
    by: Optional[Sequence[str]] = None,
) -> List[Tuple[tuple, Dict[str, Any]]]:
    """
    Combine rows per group like `fold_rows`, each group handed back with its key — for a family
    that joins or looks up the groups by key rather than serving the key as a figure.

    Args:
        rows: The rows to combine, read by attribute
        table: The fold table of their kind
        by: The row fields that form the group key — the table's grain when omitted

    Returns:
        (key, figures) per group, in the table's group order
    """
    plan = fold_plan_of(table)
    key_of = _key_reader(table.grain if by is None else tuple(by))
    groups: Dict[tuple, List[Any]] = {}
    for row in rows:
        groups.setdefault(key_of(row), []).append(row)
    keys = list(groups)
    if table.order is GroupOrder.SORTED:
        keys.sort(key=lambda k: tuple(map(str, k)))
    return [(key, _combine(groups[key], plan)) for key in keys]


def fold_group(rows: Sequence[Any], table: FoldTable) -> Dict[str, Any]:
    """
    Fold rows that already form one group — no row included, which folds every figure to its
    answer for nothing: a count to 0, every other folded figure to its declared empty value, and
    each derivation to what it rebuilds from those. A test folds no row through every table.

    Args:
        rows: The group's rows, read by attribute
        table: The fold table of their kind

    Returns:
        Figure → value
    """
    return _combine(list(rows), fold_plan_of(table))


def served_part(figures: Dict[str, Any], table: FoldTable) -> Dict[str, Any]:
    """
    The figures a table's served model takes — without the ones it folds only as inputs of its
    rates, which it names in `outside_the_fold`. A test holds every other figure to the model.

    Args:
        figures: One group's folded figures
        table: The table they were folded by

    Returns:
        The served figures
    """
    return {name: value for name, value in figures.items() if name not in table.outside_the_fold}


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
    # None stays None where the model lets a field be empty: dropped, a figure with a number for
    # its default would come back as that number — the zero a run that measured nothing never
    # booked (#646). Dropped only where the model refuses it, so a description keeps its default.
    return [RunResultRow(**{k: v for k, v in combined.items()
                            if v is not None or k in _NULLABLE_FIELDS})
            for combined in fold_rows(rows, LEDGER_TABLE, by)]


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
            value = _read(row, column)
            if value is None or value == '':
                continue
            seen.setdefault(str(value), value)
        if len(seen) > 1:
            conflicts[column] = [seen[key] for key in sorted(seen)]
    return conflicts


def fold_plan_of(table: FoldTable) -> FoldPlan:
    """
    The resolved plan of a fold table — built on first use and kept.

    Resolving checks every declaration against what the fold can apply, so a figure without a
    fold, a mean without a weight or a companion without a leader fails here, before the first
    group, instead of emptying its figure.

    Args:
        table: The fold table

    Returns:
        Its plan
    """
    plan = _PLANS.get(id(table))
    if plan is None or plan.table is not table:
        plan = _resolve(table)
        _PLANS[id(table)] = plan
    return plan


def _resolve(table: FoldTable) -> FoldPlan:
    """
    Bind each figure of a table to the function that produces it, refusing what cannot be applied.

    Args:
        table: The fold table

    Returns:
        Its plan
    """
    figures = table.figures
    unknown = set(table.derivations) - set(figures)
    if unknown:
        raise ValueError(f'{table.name}: derivations for undeclared figures {sorted(unknown)}')
    own: List[Tuple[str, Callable[[FoldInput], Any], Callable[[List[Any]], List[Any]], Any,
                    Addition]] = []
    companions: List[Tuple[str, FigureDeclaration, str]] = []
    weighted: List[Tuple[str, FigureDeclaration, FigureDeclaration]] = []
    derived: List[Tuple[str, FigureDeclaration, Derivation]] = []
    for name, declaration in figures.items():
        reduction = declaration.reduction
        if reduction in _READINGS_OF_ONE_ACCOUNT and table.account_key is None:
            raise ValueError(f'{table.name}.{name}: {reduction.name} reads one account, and the '
                             f'table names no account field')
        if reduction.folds_on_its_own():
            if reduction not in FOLD_OF_REDUCTION:
                raise ValueError(f'{table.name}.{name}: no fold for {reduction.name}')
            # A count that names no source counts the rows themselves
            if reduction is Reduction.COUNT and declaration.source is None:
                source = _each_row
            else:
                source = declaration.source or name
            own.append((name, FOLD_OF_REDUCTION[reduction], _column_reader(source, table),
                        declaration.empty_value, declaration.addition))
        elif reduction is Reduction.COMPANION:
            leader = figures.get(declaration.companion_of or '')
            if leader is None or not leader.reduction.selects() \
                    or not leader.reduction.folds_on_its_own():
                raise ValueError(f'{table.name}.{name}: a companion follows a declared figure that '
                                 f'selects one row, not {declaration.companion_of!r}')
            companions.append((name, declaration, declaration.companion_of))
        elif declaration.weighted_by is not None:
            weight = figures.get(declaration.weighted_by)
            if weight is None or name in table.derivations:
                raise ValueError(f'{table.name}.{name}: a weighted mean needs a declared weight '
                                 f'and no derivation besides')
            weighted.append((name, declaration, weight))
        else:
            if name not in table.derivations:
                raise ValueError(f'{table.name}.{name}: a DERIVE figure needs a weight or a '
                                 f'derivation')
            derived.append((name, declaration, table.derivations[name]))
    return FoldPlan(table, tuple(own), tuple(companions), tuple(weighted),
                    tuple(derived))


def _combine(rows: List[Any], plan: FoldPlan) -> Dict[str, Any]:
    """
    One group of rows as a single set of figures.

    Args:
        rows: The group's rows; none folds every figure to its answer for nothing
        plan: The resolved table

    Returns:
        Figure → value
    """
    table = plan.table
    # The rows' positions, oldest first — the same for every column of the group.
    if table.recency:
        oldest_order = sorted(range(len(rows)), key=lambda index: _recency(rows[index], table))
    else:
        oldest_order = list(range(len(rows)))
    one_account = (table.account_key is None
                   or len({getattr(row, table.account_key, None) for row in rows}) <= 1)
    # Folded before a figure's empty value applies: a companion follows the row that WON its
    # leader, and a leader no row carried was won by none.
    raw: Dict[str, Any] = {}
    combined: Dict[str, Any] = {}
    # Each column is read by the reader the plan resolved once: this loop runs once per figure
    # and group, and a lookup per cell cost the run list a third of its fold time.
    for name, fold, read_column, empty_value, addition in plan.folded_on_their_own:
        value = fold(FoldInput(read_column(rows), oldest_order, one_account, addition))
        raw[name] = value
        combined[name] = empty_value if value is None and empty_value is not None else value
    # The DERIVE and COMPANION figures need the folded values above
    _apply_companions(combined, raw, rows, plan)
    _apply_derived(combined, rows, plan)
    return combined


def _fold_sum(values: FoldInput) -> Any:
    """
    A flow or a counter: the rows partition it.

    Args:
        values: The column's values over the group

    Returns:
        The sum, or None when no row measured it
    """
    present = values.present()
    return _total(present, values.addition) if present else None


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


def _fold_count(values: FoldInput) -> int:
    """
    How many rows carry a value.

    Args:
        values: The column's values over the group

    Returns:
        The number of values that are neither None nor empty — 0, never None
    """
    return len(values.present())


def _fold_count_distinct(values: FoldInput) -> int:
    """
    How many different values the rows carry.

    Args:
        values: The column's values over the group

    Returns:
        The number of distinct values that are neither None nor empty — 0, never None
    """
    return len(set(values.present()))


def _fold_sum_complete(values: FoldInput) -> Any:
    """
    A total only over a complete set.

    Args:
        values: The column's values over the group

    Returns:
        The sum of every row's value, or None when any row did not measure it
    """
    if any(value is None for value in values.values):
        return None
    return _total(values.values, values.addition)


def _fold_first_row(values: FoldInput) -> Any:
    """
    The first row's own value, in the order the rows came, empty or not.

    Args:
        values: The column's values over the group

    Returns:
        The first row's value, or None for an empty group
    """
    return values.values[0] if values.values else None


def _fold_distinct(values: FoldInput) -> List[Any]:
    """
    The different values the rows carry, sorted, each kept as it is.

    Args:
        values: The column's values over the group

    Returns:
        The sorted distinct values that are neither None nor empty; [] when none
    """
    return sorted(set(values.present()))


def _fold_mean(values: FoldInput) -> Any:
    """
    The plain mean of the values the rows carry.

    Args:
        values: The column's values over the group

    Returns:
        Their total over their count, or None when no row carries one
    """
    present = values.present()
    return _total(present, values.addition) / len(present) if present else None


# Every reduction that folds on its own, mapped to its function. A test holds the keys to the
# members of `Reduction` that fold on their own, so a new member cannot arrive without a fold —
# and if one did, resolving a table that declares it raises instead of emptying the column.
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
    Reduction.COUNT: _fold_count,
    Reduction.COUNT_DISTINCT: _fold_count_distinct,
    Reduction.SUM_COMPLETE: _fold_sum_complete,
    Reduction.FIRST_ROW: _fold_first_row,
    Reduction.DISTINCT: _fold_distinct,
    Reduction.MEAN: _fold_mean,
}

# The reductions that read ONE account's value at an instant — undefined over several accounts,
# so a table that declares one has to say which field names a row's account.
_READINGS_OF_ONE_ACCOUNT = frozenset({Reduction.LAST, Reduction.FIRST})


def _apply_companions(
    combined: Dict[str, Any],
    raw: Dict[str, Any],
    rows: List[Any],
    plan: FoldPlan,
) -> None:
    """
    Take each companion from the row that WON its leader, never by its own extremum.

    The drawdown trio is the case: whichever period owns the deepest decline also supplies the
    peak it fell from and the share it was. Reduced separately they would pair one period's
    trough with another's peak — the defect #497 removed one layer down, and it would be back
    here the moment a caller folded them independently.

    Args:
        combined: The figures being built, modified in place
        raw: The folded values before any empty value applied — what the leader actually won
        rows: The group
        plan: The resolved table
    """
    figures = plan.table.figures
    for name, declaration, leader in plan.companions:
        winner = raw.get(leader)
        if winner is None:
            combined[name] = _or_empty(None, declaration)
            continue
        leader_source = figures[leader].source or leader
        source = next((row for row in rows if _read(row, leader_source) == winner), None)
        value = _read(source, declaration.source or name) if source is not None else None
        combined[name] = _or_empty(value, declaration)


def _apply_derived(combined: Dict[str, Any], rows: List[Any], plan: FoldPlan) -> None:
    """
    Rebuild every DERIVE figure from the folded components — never from the rows' own values.

    A rate cannot be folded out of two periods; its COMPONENTS can be added on any level, which
    is the whole reason the ledger carries `gross_profit` / `gross_loss` beside the profit factor
    and the trade counts beside the win rate. A mean declares the count it was taken over and is
    rebuilt as the mean weighted by it; every other DERIVE figure has its derivation in its table.

    Args:
        combined: The figures being built, modified in place
        rows: The group, for the weights a mean needs
        plan: The resolved table
    """
    for name, declaration, weight in plan.weighted_means:
        value = _weighted_mean(rows, declaration.source or name,
                               weight.source or declaration.weighted_by, declaration.addition)
        combined[name] = _or_empty(value, declaration)
    for name, declaration, derivation in plan.derived:
        combined[name] = _or_empty(derivation(combined, rows), declaration)


def _weighted_mean(
    rows: List[Any],
    source: FigureSource,
    weight_source: FigureSource,
    addition: Addition,
) -> Optional[float]:
    """
    The mean of a per-row mean, weighted by the population it was measured over.

    Args:
        rows: The group
        source: Where each row's mean is read
        weight_source: Where the count that mean was taken over is read
        addition: How the weighted values and the weights are added

    Returns:
        The weighted mean, or None when nothing was measured
    """
    if addition is Addition.SEQUENTIAL:
        total_weight = 0.0
        total_value = 0.0
        for row in rows:
            value = _read(row, source)
            weight = _read(row, weight_source) or 0
            if value is None or weight <= 0:
                continue
            total_value += value * weight
            total_weight += weight
        return (total_value / total_weight) if total_weight else None
    pairs = [(value, weight) for value, weight in
             ((_read(row, source), _read(row, weight_source) or 0) for row in rows)
             if value is not None and weight > 0]
    total_weight = sum(weight for _, weight in pairs)
    return (sum(value * weight for value, weight in pairs) / total_weight) if total_weight else None


def _total(values: List[Any], addition: Addition) -> Any:
    """
    The values added the way their figure declares.

    Args:
        values: The values, none of them empty
        addition: Compensated (the built-in sum), sequential (a loop from 0.0) or exact (fsum)

    Returns:
        Their total
    """
    if addition is Addition.COMPENSATED:
        return sum(values)
    if addition is Addition.EXACT:
        return math.fsum(values)
    total = 0.0
    for value in values:
        total += value
    return total


def _key_reader(by: Tuple[str, ...]) -> Callable[[Any], tuple]:
    """
    The function that reads a row's group key — built once per fold, not once per row.

    Args:
        by: The row fields that form the key

    Returns:
        A function from a row to its key tuple; a missing field reads ''
    """
    if not by:
        return lambda row: ()
    if len(by) == 1:
        only = by[0]
        return lambda row: (getattr(row, only, ''),)
    if len(by) == 2:
        first, second = by
        return lambda row: (getattr(row, first, ''), getattr(row, second, ''))
    return lambda row: tuple(getattr(row, key, '') for key in by)


def _column_reader(source: FigureSource, table: FoldTable) -> Callable[[List[Any]], List[Any]]:
    """
    The function that reads one figure's column over a group — resolved once per table.

    A field of a typed row model is read through `attrgetter`, which a test holds to the model's
    fields; a table without one (the ledger) reads with a default, because its rows may lack a
    column the catalog names.

    Args:
        source: A field name, or a named function of the row
        table: The figure's table

    Returns:
        A function from the group's rows to the figure's values, in row order
    """
    if callable(source):
        return lambda rows: list(map(source, rows))
    if table.row_model is not None:
        getter = attrgetter(source)
        return lambda rows: list(map(getter, rows))
    return lambda rows: [getattr(row, source, None) for row in rows]


def _each_row(row: Any) -> bool:
    """
    The source of a count that names none: every row counts.

    Args:
        row: The row

    Returns:
        True
    """
    return True


def _or_empty(value: Any, declaration: FigureDeclaration) -> Any:
    """
    A figure's value, or its declared empty value when it has none.

    Args:
        value: The folded value
        declaration: The figure's declaration

    Returns:
        The value; the declared empty value in place of None
    """
    if value is None and declaration.empty_value is not None:
        return declaration.empty_value
    return value


def _recency(row: Any, table: FoldTable) -> str:
    """
    The stamp that orders a row against its siblings.

    Args:
        row: The row
        table: Its fold table, which names the stamps

    Returns:
        The first recency stamp it carries, or '' when it carries none
    """
    for key in table.recency:
        value = _read(row, key)
        if value:
            return str(value)
    return ''


def _read(row: Any, source: FigureSource) -> Any:
    """
    One figure's value on a row, through its declared source.

    Read through `getattr` with a default for every field, so a field a row's model spells
    differently reads empty in this one place rather than raising in every fold.

    Args:
        row: The row, or None
        source: A field name, or a named function of the row

    Returns:
        The value, or None
    """
    if row is None:
        return None
    if callable(source):
        return source(row)
    return getattr(row, source, None)


# Each table's plan, resolved on first use.
_PLANS: Dict[int, FoldPlan] = {}

# The ledger's plan is resolved when this module loads, so a ledger figure the fold cannot apply
# fails at import, as it always has.
fold_plan_of(LEDGER_TABLE)
