"""
Aggregation types (#557) — the vocabulary every combined figure is declared in.

`Reduction` says how a figure combines when several rows are read as one, `FigureKind` what kind of
figure that makes it, and `NullMeaning` what its empty cell means. `FigureDeclaration` holds the
answers for one figure; a `FoldTable` holds the declarations of one kind of row — the ledger's in
the figure catalog (`reporting/aggregation/figure_catalog.py`), every other one under
`reporting/aggregation/tables/` — and the fold applies them.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union


class FigureKind(Enum):
    """
    What kind of figure a ledger column holds — which decides whether it may cross accounts.

    A flow adds up over any rows. A ratio is never combined, only re-derived from the flows of the
    rows it describes. A reading belongs to one account — a stock, a peak, a trough, an extreme —
    and has no value over several. A description is no figure at all. A total over several
    composites carries flows only (#615).
    """
    FLOW = 'flow'
    RATIO = 'ratio'
    ACCOUNT_READING = 'account_reading'
    DESCRIPTION = 'description'


class Reduction(Enum):
    """
    How a ledger column combines when several rows are read as one.

    The question a reader actually has is not "is this aggregated" but "how do I combine it",
    and the two do not line up: `net_pnl` and `win_rate` are both aggregates, one sums and the
    other cannot be combined at all. Marking both as aggregates would make them look alike,
    which is precisely the confusion that produces a wrong total (CLAUDE.md §48).

    Declared per figure in the figure catalog (`figure_catalog.LEDGER_FIGURES`), and a test holds
    the catalog and LEDGER_COLUMNS to the same key set — so a new column cannot be added without
    saying how it reduces.
    """
    SUM = 'sum'             # a flow or a counter: rows partition it, adding them is correct
    MAX = 'max'             # a plain maximum: a peak, a high count. NEVER sum
    # The largest by MAGNITUDE, sign kept — for a figure whose worst value may be stored either
    # way round. A decline is the case: `account_max_drawdown` holds a magnitude,
    # `period_max_drawdown` held a negative until #539, and rows of both ages sit in one store.
    # It was `MAX` doing this silently, and the name is why: a reader who sees MAX reads `max()`
    # and is right about the columns that mean a peak and wrong about the ones that mean a fall.
    # Measured 2026-09-22 — that misreading produced a defect report against correct code, and
    # the same constant on `period_max_equity` would have picked a NEGATIVE equity over a
    # positive one, which nothing had noticed because no account has gone negative yet.
    MAX_ABS = 'max_abs'
    DERIVE = 'derive'       # a rate, a mean, a quotient: NOT combinable, re-derive from records
    # A stock read at an instant, of ONE account: the most recent row wins. Over the rows of
    # several accounts — the scenarios of one backtest — it has no value at all, because no
    # account ever held their latest readings together; the fold answers None rather than
    # whichever account happened to close last (measured 2026-09-29: a sweep row carried one
    # scenario's equity out of eight).
    LAST = 'last'
    # A stock read at the OPENING instant, of one account: the earliest ROW's value — the account
    # a unit began its window with. Unlike LAST it does not skip an unmeasured row: when the
    # earliest did not record it, the opening is unknown. Undefined over several accounts.
    FIRST = 'first'
    IDENTITY = 'identity'   # must agree across the rows, or they were never comparable
    UNION = 'union'         # a comma-joined set: combine by union, never by concatenation
    MIN = 'min'             # a plain minimum — the trough, the mirror of MAX
    # An instant that is one END of a range. `SPAN` alone was not enough and the gap only showed
    # when something finally READ the map: over several rows the earliest and the latest both
    # mean something, while an aggregated row has ONE slot — so the declaration has to say which
    # end belongs in it, or every caller decides for itself and they disagree.
    SPAN_START = 'span_start'   # the earliest — a beginning, an opening
    SPAN_END = 'span_end'       # the latest — an end, a close, a stamp
    # Reduced WITH another column, never on its own. The drawdown trio is the case: whichever
    # row owns the deepest decline also supplies the peak it fell from and the share it was,
    # and taking each by its own max pairs one row's trough with another's peak — exactly the
    # defect #497 removed from the console aggregate (report_aggregators.py:160-163 states it).
    # The column it follows is declared beside it (`FigureDeclaration.companion_of`).
    COMPANION = 'companion'
    # How many rows carry the value — every row when the figure names no source. A count with a
    # condition is a SUM over a 0/1 source instead: False is a value, and COUNT would count it.
    COUNT = 'count'
    # How many DIFFERENT values the rows carry — runs, where one run writes many rows. Never
    # added across groups: two groups can hold the same value, so it is re-derived from the rows.
    COUNT_DISTINCT = 'count_distinct'
    # A total only over a complete set: None as soon as one row did not measure it, because a
    # sum over the rows that did would claim a figure for the ones that did not.
    SUM_COMPLETE = 'sum_complete'
    # The FIRST ROW's own value, in the order the rows came, as it is — no age order, no skipping
    # of an empty value. What a hand-written aggregate read as `group[0]`. IDENTITY skips an empty
    # first row and FIRST orders by age and reads one account; neither is that. It is also how a
    # table reads its grain: every row of a group carries the key, an empty one included.
    FIRST_ROW = 'first_row'
    # The different values the rows carry, as a sorted list, each kept as it is. UNION is the
    # ledger's comma-joined set; this one keeps the members' own type.
    DISTINCT = 'distinct'
    # The plain mean of the values the rows carry, each row one event of equal weight — a fill,
    # a trade. A mean over rows that are themselves means is a mean of means and wants `weighted_by`
    # instead; where one survives, its declaration says so.
    MEAN = 'mean'

    def figure_kind(self) -> FigureKind:
        """
        The kind of figure a column with this reduction holds.

        Returns:
            Its FigureKind, declared per member in `_FIGURE_KIND_OF`
        """
        return _FIGURE_KIND_OF[self]

    def folds_on_its_own(self) -> bool:
        """
        Whether a column with this reduction is folded from its own values alone.

        The two that are not are rebuilt from the folded values of OTHER columns, so they are
        applied after every other column is folded: a rate from its components, a companion from
        the row that won its leader.

        Returns:
            False for DERIVE and COMPANION, True for every other member
        """
        return self not in _REBUILT_FROM_OTHER_COLUMNS

    def selects(self) -> bool:
        """
        Whether the folded value is one the rows carried — chosen among them, never computed.

        A companion can only follow a column that selects: it is taken from the row whose value
        won, and a computed value was won by no row.

        Returns:
            True for the members that pick one row's value
        """
        return self in _SELECTING


# The kind of figure behind each reduction (#615). Every member is listed, and a test holds the
# map to the enum, so a new reduction cannot arrive without saying whether its columns may cross
# accounts — a member missing here would raise rather than fall into a default kind.
_FIGURE_KIND_OF: Dict[Reduction, FigureKind] = {
    Reduction.SUM: FigureKind.FLOW,
    Reduction.DERIVE: FigureKind.RATIO,
    Reduction.MAX: FigureKind.ACCOUNT_READING,
    Reduction.MAX_ABS: FigureKind.ACCOUNT_READING,
    Reduction.MIN: FigureKind.ACCOUNT_READING,
    Reduction.LAST: FigureKind.ACCOUNT_READING,
    Reduction.FIRST: FigureKind.ACCOUNT_READING,
    Reduction.COMPANION: FigureKind.ACCOUNT_READING,
    Reduction.IDENTITY: FigureKind.DESCRIPTION,
    Reduction.UNION: FigureKind.DESCRIPTION,
    Reduction.SPAN_START: FigureKind.DESCRIPTION,
    Reduction.SPAN_END: FigureKind.DESCRIPTION,
    Reduction.COUNT: FigureKind.FLOW,
    Reduction.SUM_COMPLETE: FigureKind.FLOW,
    # Re-derived from the rows like a rate: two groups' counts do not add up when they share values
    Reduction.COUNT_DISTINCT: FigureKind.RATIO,
    Reduction.FIRST_ROW: FigureKind.DESCRIPTION,
    Reduction.DISTINCT: FigureKind.DESCRIPTION,
    Reduction.MEAN: FigureKind.RATIO,
}

# Rebuilt from other columns' folded values (`Reduction.folds_on_its_own`). A test holds the members
# that fold on their own to the fold's table, so a new member that is in neither raises in the fold.
_REBUILT_FROM_OTHER_COLUMNS: FrozenSet[Reduction] = frozenset({Reduction.DERIVE, Reduction.COMPANION})

# The members whose fold picks one row's value (`Reduction.selects`).
_SELECTING: FrozenSet[Reduction] = frozenset({
    Reduction.MAX, Reduction.MAX_ABS, Reduction.MIN, Reduction.LAST, Reduction.FIRST,
    Reduction.IDENTITY, Reduction.SPAN_START, Reduction.SPAN_END, Reduction.FIRST_ROW,
})


class NullMeaning(Enum):
    """
    What the empty cell of a nullable figure means — declared per figure in the figure catalog
    (`FigureDeclaration.null_meaning`), which a test holds to the nullable figures.

    A null can mean two things, and a reader who takes one for the other decides wrongly: a
    ranking that leaves out the runs whose profit factor is undefined drops the runs that never
    lost, and one that refuses a figure merely missing from some rows refuses a question it could
    answer over the others.
    """
    # No value exists for the row: a ratio or a mean with nothing to divide by — a profit factor
    # without a losing trade — a figure about something the run did not have — a signal ratio
    # without a signal worker — or a reading of one account over the rows of several. A ranking
    # cannot place such a row, so it refuses the objective.
    UNDEFINED = 'undefined'
    # A value existed and was not recorded: a row written before its column existed, a reading
    # taken before the account's first valuation. A ranking leaves the row out and counts it.
    UNMEASURED = 'unmeasured'


class Addition(Enum):
    """
    How a figure's values are added — the two ways give different last bits.

    Since Python 3.12 the built-in `sum()` adds floats with error compensation, while a loop that
    adds one value at a time does not: `[0.1, 0.2, 0.3]` sums to 0.6 one way and to
    0.6000000000000001 the other (measured on 3.12.11). The hand-written aggregates used both, and
    a fold that moves them must keep each one's way or the served numbers change in their last
    bit. Which way a figure adds is therefore declared, not assumed.
    """
    COMPENSATED = 'compensated'     # the built-in sum()
    SEQUENTIAL = 'sequential'       # a loop from 0.0, one value at a time — #657 retires it
    EXACT = 'exact'                 # math.fsum, exactly rounded — how statistics.fmean adds


class GroupOrder(Enum):
    """
    The order a fold hands back its groups in. A listing ordered by anything else — by a figure,
    by an enum's declared order — is sorted by the family that serves it, as before.
    """
    SORTED = 'sorted'               # by the group key, compared as strings
    FIRST_SEEN = 'first_seen'       # in the order the groups first appear in the rows


# A figure's value when no row carries one, where a served field kept the old habit of a number
# instead of None — #657 retires each one.
EmptyValue = Union[float, int, str, bool, None]

# Where a figure reads its value on a row: the field of that name, or a named function of the row.
FigureSource = Union[str, Callable[[Any], Any], None]


@dataclass(frozen=True)
class FigureDeclaration:
    """
    How one figure combines when several rows are read as one, and what its empty cell means.

    Args:
        reduction: How it folds
        null_meaning: What its empty cell means. A ranking reads it: it refuses a figure that can
            be undefined for a run, and ranks one that was only not measured over the rows that
            carry it, saying how many it left out. Where a null can mean both — a set that can be
            empty, or a row from before the column — it is declared UNDEFINED, because leaving
            out the runs that never lost is the worse mistake. None for a figure the row model
            never leaves empty, and for a description
        companion_of: The column a COMPANION is reduced with — the one whose winning row
            supplies it. None for every other reduction
        weighted_by: For a mean, the count it was taken over. The combined mean is weighted by
            that population, so it is the mean the same records would have produced in one pass —
            an unweighted average of averages would let a period with two trades count as much
            as one with two hundred. None for everything that is no mean
        source: Where the value is read on a row — a field name, or a named function of the row
            for a per-row projection (a magnitude, a count of a list, a value kept only for
            winners). None reads the field the figure is named after
        empty_value: What the figure answers when no row carries a value. None by the rule; 0 for
            a count over no rows, which is the truth; any other number or '' only where a served
            field kept that habit, which #657 retires. Applied when the figure is folded, so a
            derivation reads the figure as served
        addition: How its values are added — the built-in sum() unless the aggregate it replaces
            added in a loop (`Addition`)
    """
    reduction: Reduction
    null_meaning: Optional[NullMeaning] = None
    companion_of: Optional[str] = None
    weighted_by: Optional[str] = None
    source: FigureSource = None
    empty_value: EmptyValue = None
    addition: Addition = Addition.COMPENSATED


@dataclass(slots=True)
class FoldInput:
    """
    One column's values over a group of rows, as a reduction reads them.

    Built once per column and group, so it is slotted and not frozen: a frozen dataclass costs
    three times as much to build, and the fold builds one for every column of every group.

    Args:
        values: Every row's value, in the order the rows came, empty cells kept
        oldest_order: The rows' positions in `values`, oldest row first — the same for every
            column of the group
        one_account: Whether every row belongs to one unit — a reading of an account at an
            instant is only defined then
        addition: How the column's values are added
    """
    values: List[Any]
    oldest_order: List[int]
    one_account: bool
    addition: Addition = Addition.COMPENSATED

    def present(self) -> List[Any]:
        """
        The values the rows carry, in the order the rows came.

        Returns:
            Every value that is neither None nor empty
        """
        return [value for value in self.values if value is not None and value != '']

    def oldest_first(self) -> List[Any]:
        """
        Every row's value, oldest row first, empty cells kept — an opening is the oldest row's own
        value, and an empty one there means the opening is unknown.

        Returns:
            The values in the order the rows were recorded
        """
        return [self.values[index] for index in self.oldest_order]


# A derivation: one figure rebuilt from the group's folded values and its rows.
Derivation = Callable[[Dict[str, Any], Sequence[Any]], Any]


@dataclass(frozen=True, eq=False)
class FoldTable:
    """
    The declarations of one kind of row, and what the fold needs to apply them.

    Compared and hashed by identity: a table is a declaration that exists once, and the fold keeps
    its resolved plan per table.

    Args:
        name: The table's name — a ledger row, a trade, a portfolio unit
        figures: Every figure the fold produces, each with its declaration, in output order
        derivations: For each DERIVE figure that is no weighted mean, how it is rebuilt from the
            group's folded values and its rows
        account_key: The row field that names the account a row belongs to — a reading of one
            account (LAST, FIRST) is only defined when the group holds one. None when the rows
            carry no account; such a table declares no reading of one
        recency: The row fields that order a row against its siblings, the first one a row carries
            winning. Empty when the rows' own order is their age
        order: The order the groups come back in
        served_as: The model one group is served as, when its fields are the figures plus the
            ones named in `outside_the_fold` — a test holds the two to each other, both ways.
            None where another test holds the figures (the ledger's columns)
        outside_the_fold: The fields on one side only, each with why — served but not folded (a
            group's identity or an input its family passes in, a copy, a listing), or folded but
            not served (the key a list is indexed by)
        row_model: The model of one row, so a test can tell which figures can come back empty —
            a figure read from a field that is never empty never is
        grain: The row fields one group is keyed by — what one served entry is: one per sweep,
            one per run. The fold groups by it unless a caller names another key; its family folds
            only rows that carry it. Empty where a family hands over the rows of one entry, or
            where the table folds at several grains (the ledger)
        serves_whole: False for a table that folds one PART of a served model — its execution
            counts, its pending orders — beside the table that names the whole; its own
            `outside_the_fold` then names only the figures it folds and the model does not serve
    """
    name: str
    figures: Dict[str, FigureDeclaration]
    derivations: Dict[str, Derivation] = field(default_factory=dict)
    account_key: Optional[str] = None
    recency: Tuple[str, ...] = ()
    order: GroupOrder = GroupOrder.SORTED
    served_as: Optional[type] = None
    outside_the_fold: Dict[str, str] = field(default_factory=dict)
    row_model: Optional[type] = None
    grain: Tuple[str, ...] = ()
    serves_whole: bool = True


@dataclass(frozen=True)
class FoldPlan:
    """
    A fold table resolved once: each figure bound to the function that produces it. Built by the
    fold (`aggregation_fold.fold_plan_of`), which refuses a declaration it cannot apply.

    Args:
        table: The table it was resolved from
        folded_on_their_own: Each figure folded from its own values: its name, its fold, the
            reader of its column, its empty value and its addition — taken out of the declaration
            once, because the fold reads them for every figure of every group
        companions: Each COMPANION with its declaration and the leader it follows
        weighted_means: Each weighted mean with its declaration and its weight's declaration
        derived: Each other DERIVE figure with its declaration and derivation
    """
    table: FoldTable
    folded_on_their_own: Tuple[
        Tuple[str, Callable[[FoldInput], Any], Callable[[List[Any]], List[Any]], EmptyValue,
              Addition], ...]
    companions: Tuple[Tuple[str, FigureDeclaration, str], ...]
    weighted_means: Tuple[Tuple[str, FigureDeclaration, FigureDeclaration], ...]
    derived: Tuple[Tuple[str, FigureDeclaration, Derivation], ...]
