"""
Aggregation types (#557) — the vocabulary every combined figure is declared in.

`Reduction` says how a figure combines when several rows are read as one, `FigureKind` what kind of
figure that makes it, and `NullMeaning` what its empty cell means. `FigureDeclaration` holds the
three answers for one figure; the figure catalog (`reporting/aggregation/figure_catalog.py`)
declares one per figure, and the fold applies them.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional


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
}

# Rebuilt from other columns' folded values (`Reduction.folds_on_its_own`). A test holds the members
# that fold on their own to the fold's table, so a new member that is in neither raises in the fold.
_REBUILT_FROM_OTHER_COLUMNS: FrozenSet[Reduction] = frozenset({Reduction.DERIVE, Reduction.COMPANION})

# The members whose fold picks one row's value (`Reduction.selects`).
_SELECTING: FrozenSet[Reduction] = frozenset({
    Reduction.MAX, Reduction.MAX_ABS, Reduction.MIN, Reduction.LAST, Reduction.FIRST,
    Reduction.IDENTITY, Reduction.SPAN_START, Reduction.SPAN_END,
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
    """
    reduction: Reduction
    null_meaning: Optional[NullMeaning] = None
    companion_of: Optional[str] = None
    weighted_by: Optional[str] = None


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
    """
    values: List[Any]
    oldest_order: List[int]
    one_account: bool

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
