"""
Every ledger column, through its declared reduction, against invariants that follow from it.

The map says how each column combines and a completeness test says none is missing. What
nothing checked was whether the FOLD obeys the class it declares — and on 2026-09-22 that gap
cost real time: `Reduction.MAX` was `max(key=abs)`, a reader took the name at face value,
reported a defect against correct code and "fixed" it. The same constant also sat on a column
that wants a plain maximum.

**These assert PROPERTIES of the class, never the expected value per column.** Restating the
answer for each of ~80 columns would be a second copy of `COLUMN_REDUCTION`, and two copies of
one rule is the pair §19 exists to prevent. The honest limit follows from that and is worth
stating: a property test confirms the declaration is APPLIED correctly, and cannot know whether
the declaration is the RIGHT one for that column. `segment_max_equity` under `MAX_ABS` would
satisfy every assertion here. Catching a wrong ASSIGNMENT needs per-column knowledge, which is
the map itself.

What they do catch: a broken implementation, a member with no branch, an `abs()` creeping in,
a `min` in a `max` slot, and any reduction that depends on the order rows happen to arrive in.
"""

from typing import Any, List

import pytest

from python.framework.reporting.store.ledger_aggregation import aggregate_ledger_rows
from python.framework.reporting.store.run_results_ledger import (
    COLUMN_COMPANION_OF,
    COLUMN_REDUCTION,
)
from python.framework.types.api.report_types import RunResultRow
from python.framework.types.run_results_types import Reduction

_RUN = '20260922_000000_deadbeef'

# The grouping keys: folded rows are grouped BY these, so a pair that differs in one of them is
# two groups rather than one input set.
_GROUPING = ('run_id', 'currency')

# Not combined at all — the enum's own comments say why, and `_reduce` returns None for them.
_NOT_COMBINED = {Reduction.DERIVE, Reduction.COMPANION}

# Order is the whole content of these three: the earliest, the latest, the most recent.
# IDENTITY joins them for a different reason: its contract is that the rows AGREE, so feeding
# it two different values asks a question the declaration forbids. What it actually does with
# disagreeing rows is pinned on its own below — that is §49's open example, not a property.
_ORDER_SENSITIVE = {Reduction.LAST, Reduction.SPAN_START, Reduction.SPAN_END,
                    Reduction.IDENTITY}

# The result of these must be a value that actually occurred — they SELECT, they do not compute.
_SELECTORS = {Reduction.MAX, Reduction.MAX_ABS, Reduction.MIN, Reduction.LAST,
              Reduction.IDENTITY, Reduction.SPAN_START, Reduction.SPAN_END}


def _numeric_columns() -> List[str]:
    """
    Every declared column whose model field is a plain number.

    Derived from `RunResultRow`'s annotations rather than from a list here — a list would be the
    third copy of the schema, and the two that exist are already held to each other by a test.

    Returns:
        Column names carrying an int or float field, excluding the grouping keys and the
        columns that are not combined at all
    """
    columns = []
    for name, reduction in COLUMN_REDUCTION.items():
        if name in _GROUPING or reduction in _NOT_COMBINED or name in COLUMN_COMPANION_OF:
            continue
        field = RunResultRow.model_fields.get(name)
        if field is None:
            continue
        annotation = str(field.annotation)
        if 'int' in annotation or 'float' in annotation:
            if 'bool' in annotation or 'str' in annotation:
                continue
            columns.append(name)
    return columns


def _rows(column: str, values: List[Any]) -> List[RunResultRow]:
    """
    One row per value, identical in everything else.

    Args:
        column: The column under test
        values: Its value in each row

    Returns:
        Rows ready to fold as one group
    """
    rows = []
    for index, value in enumerate(values):
        # Everything but the column under test is held constant — except the two stamps, which
        # give the order-sensitive reductions something to be sensitive ABOUT.
        fields = {
            'run_id': _RUN,
            'param_hash': 'p',
            'currency': 'USD',
            'run_timestamp': f'2026-09-2{index + 1}T00:00:00+00:00',
            'recorded_at_utc': f'2026-09-2{index + 1}T12:00:00+00:00',
            'segment_no': index + 1,
        }
        fields[column] = value
        rows.append(RunResultRow(**fields))
    return rows


def _fold(column: str, values: List[Any]) -> Any:
    """
    Fold the rows and read the column back.

    Args:
        column: The column under test
        values: One value per row

    Returns:
        The combined value
    """
    combined = aggregate_ledger_rows(_rows(column, values), by=('run_id', 'currency'))
    return getattr(combined[0], column)


NUMERIC = _numeric_columns()


def test_the_sweep_actually_covers_something():
    """A generated list that silently came back empty would make every case below vacuous."""
    assert len(NUMERIC) > 20


@pytest.mark.parametrize('column', NUMERIC)
class TestEveryNumericColumn:

    def test_folding_one_row_returns_its_value(self, column):
        # Idempotence. Cheap, and it catches a reduction that computes where it should select.
        assert _fold(column, [7.0]) == 7.0

    def test_the_order_rows_arrive_in_does_not_change_the_answer(self, column):
        if COLUMN_REDUCTION[column] in _ORDER_SENSITIVE:
            pytest.skip('order IS the content of this reduction')
        assert _fold(column, [3.0, 11.0]) == _fold(column, [11.0, 3.0])

    def test_a_selector_never_invents_a_value(self, column):
        if COLUMN_REDUCTION[column] not in _SELECTORS:
            pytest.skip('this reduction computes rather than selects')
        if COLUMN_REDUCTION[column] is Reduction.SPAN_END and column == 'segment_no':
            pytest.skip('its own value is the ordering key here, so the pair cannot vary')
        assert _fold(column, [3.0, 11.0]) in (3.0, 11.0)

    def test_the_sign_survives(self, column):
        # All inputs negative → the answer cannot be positive, whatever the class. This is the
        # family that cost the day: a magnitude reading that quietly drops a sign, or a plain
        # comparison over values stored the other way round.
        assert _fold(column, [-3.0, -11.0]) <= 0
        assert _fold(column, [3.0, 11.0]) >= 0


class TestTheTwoMaximaDifferWhereItMatters:
    """
    `MAX` selects the largest, `MAX_ABS` the largest by magnitude. Over positive values they are
    identical, which is why one name served both for so long — and why the cases below use
    negatives, where they part company.
    """

    def test_max_takes_the_largest(self):
        assert _fold('segment_max_equity', [-9.0, -1.0]) == -1.0

    def test_max_abs_takes_the_deepest(self):
        assert _fold('segment_max_drawdown', [-9.0, -1.0]) == -9.0

    def test_they_agree_over_magnitudes(self):
        assert _fold('segment_max_equity', [9.0, 1.0]) == 9.0
        assert _fold('segment_max_drawdown', [9.0, 1.0]) == 9.0

    def test_adding_a_row_can_only_deepen_a_magnitude(self):
        two = abs(_fold('segment_max_drawdown', [-3.0, -11.0]))
        three = abs(_fold('segment_max_drawdown', [-3.0, -11.0, -40.0]))
        assert three >= two


class TestWhatIdentityDoesNotDo:
    """
    The open example §49 names: an IDENTITY column says the rows must AGREE, and nothing checks
    that they do. Pinned here rather than left as prose, so the limit is visible from the tests
    and a future gate has something to replace.
    """

    def test_identity_takes_the_first_row_without_checking(self):
        # `param_hash` is IDENTITY. Two rows that disagree are two runs that were never
        # comparable, and the fold reports one of them without a word.
        assert COLUMN_REDUCTION['param_hash'] is Reduction.IDENTITY
        rows = [
            RunResultRow(run_id=_RUN, currency='USD', param_hash='aaa',
                         run_timestamp='2026-09-21T00:00:00+00:00'),
            RunResultRow(run_id=_RUN, currency='USD', param_hash='bbb',
                         run_timestamp='2026-09-22T00:00:00+00:00'),
        ]
        combined = aggregate_ledger_rows(rows, by=('run_id', 'currency'))[0]
        assert combined.param_hash in ('aaa', 'bbb')
