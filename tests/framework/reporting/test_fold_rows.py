"""
The fold over any table (#557): every declared table resolves, and the fold applies a figure's
source, its empty value and its kind of addition exactly as declared.
"""

import ast
import statistics
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, get_args, get_type_hints

import pytest

from python.framework.reporting.aggregation import figure_tables
from python.framework.reporting.aggregation.aggregation_fold import (
    fold_group,
    fold_plan_of,
    fold_rows,
)
from python.framework.reporting.aggregation.figure_tables import FIGURE_TABLES, SERVED_UNFOLDED
from python.framework.types.aggregation_types import (
    Addition,
    FigureDeclaration,
    FigureKind,
    FoldTable,
    GroupOrder,
    Reduction,
)


@dataclass
class _Row:
    """A row of the test table."""
    group: str
    account: str
    amount: Optional[float]
    peak: Optional[float] = None
    label: str = ''
    weight: int = 0


def _magnitude(row: _Row) -> Optional[float]:
    """The amount's magnitude — a per-row projection, as a named source."""
    return None if row.amount is None else abs(row.amount)


def _share_of_peak(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """A derivation that reads a folded figure as served, empty value included."""
    return combined['biggest'] * 2


_TABLE = FoldTable(
    name='test',
    figures={
        'group': FigureDeclaration(Reduction.IDENTITY),
        'amount': FigureDeclaration(Reduction.SUM),
        'amount_loop': FigureDeclaration(Reduction.SUM, source='amount',
                                         addition=Addition.SEQUENTIAL),
        'biggest': FigureDeclaration(Reduction.MAX, source=_magnitude, empty_value=0.0),
        'peak': FigureDeclaration(Reduction.MAX_ABS),
        'peak_label': FigureDeclaration(Reduction.COMPANION, companion_of='peak',
                                        source='label', empty_value=''),
        'doubled': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={'doubled': _share_of_peak},
    account_key='account',
    order=GroupOrder.FIRST_SEEN,
)


# Where the fold tables are defined — walked to hold the index complete.
_TABLES_DIR = Path(__file__).resolve().parents[3] / 'python/framework/reporting/aggregation/tables'

# The tables a model is served from — the ledger's figures are held to its columns elsewhere.
_SERVED = [name for name, table in FIGURE_TABLES.items() if table.served_as is not None]

# The reductions whose fold answers a value for any group, never None.
_NEVER_EMPTY = frozenset({Reduction.COUNT, Reduction.COUNT_DISTINCT, Reduction.DISTINCT})


def _fields_of(model: type) -> set:
    """The fields a served model declares — a Pydantic model or a dataclass."""
    if is_dataclass(model):
        return {f.name for f in fields(model)}
    return set(model.model_fields)


def _may_be_none(annotation: Any) -> bool:
    """Whether a type annotation lets the value be None — no annotation, or Any, counts as yes."""
    return annotation is None or annotation is Any or type(None) in get_args(annotation)


def _source_may_be_empty(table: FoldTable, source: Any) -> bool:
    """
    Whether a figure's source can read None on a row: a function says so in its return type, a
    field in the row model's annotations (a Pydantic model or a dataclass). A text field that is never None is taken as never empty — a family
    folds only rows whose key it carries.
    """
    if callable(source):
        return _may_be_none(get_type_hints(source).get('return'))
    return _may_be_none(get_type_hints(table.row_model)[source])


def _can_answer_empty(table: FoldTable, figure: str) -> bool:
    """Whether the fold can answer None for a figure of a non-empty group."""
    declaration = table.figures[figure]
    if declaration.empty_value is not None or declaration.reduction in _NEVER_EMPTY:
        return False
    if (declaration.source or figure) in table.grain:
        return False    # a family folds only the rows that carry their key
    if declaration.reduction is Reduction.DERIVE:
        if declaration.weighted_by is not None:
            return True
        return _may_be_none(get_type_hints(table.derivations[figure]).get('return'))
    if declaration.reduction is Reduction.COMPANION:
        if _can_answer_empty(table, declaration.companion_of):
            return True
    return _source_may_be_empty(table, declaration.source or figure)


class TestEveryTableResolves:
    """Each table in the catalog is held to what the fold can apply."""

    @pytest.mark.parametrize('name', sorted(FIGURE_TABLES))
    def test_the_table_resolves(self, name):
        fold_plan_of(FIGURE_TABLES[name])

    @pytest.mark.parametrize('name', sorted(FIGURE_TABLES))
    def test_a_weight_is_a_count_that_adds_up(self, name):
        figures = FIGURE_TABLES[name].figures
        for figure, declaration in figures.items():
            if declaration.weighted_by is not None:
                weight = figures[declaration.weighted_by]
                assert weight.reduction.figure_kind() is FigureKind.FLOW, (name, figure)

    @pytest.mark.parametrize('name', sorted(FIGURE_TABLES))
    def test_only_a_derived_figure_has_a_derivation(self, name):
        table = FIGURE_TABLES[name]
        for figure in table.derivations:
            assert table.figures[figure].reduction is Reduction.DERIVE, (name, figure)

    def test_every_table_is_listed_under_its_own_name(self):
        for name, table in FIGURE_TABLES.items():
            assert table.name == name

    def test_every_table_defined_under_tables_is_listed(self):
        # The index is written by hand, so the source tree is walked: a table defined and not
        # listed would escape every check here and still be served.
        listed = {id(table) for table in FIGURE_TABLES.values()}
        defined = []
        for path in sorted(_TABLES_DIR.glob('*.py')):
            for node in ast.parse(path.read_text(encoding='utf-8')).body:
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                        and getattr(node.value.func, 'id', '') == 'FoldTable'):
                    defined.extend((path.name, target.id) for target in node.targets)
        assert defined
        for file_name, name in defined:
            table = getattr(figure_tables, name, None)
            assert table is not None and id(table) in listed, (file_name, name)

    @pytest.mark.parametrize('name', sorted(FIGURE_TABLES))
    def test_no_row_folds_without_raising(self, name):
        # What the fold promises for an empty group: every figure answers for nothing, and every
        # derivation rebuilds from those answers instead of meeting None.
        fold_group([], FIGURE_TABLES[name])

    @pytest.mark.parametrize('name', sorted(n for n, t in FIGURE_TABLES.items() if t.row_model))
    def test_every_source_names_a_field_of_the_row(self, name):
        table = FIGURE_TABLES[name]
        fields_of_row = set(get_type_hints(table.row_model))
        for field_name in table.grain:
            assert field_name in fields_of_row, (name, field_name)
        for figure, declaration in table.figures.items():
            if callable(declaration.source):
                continue
            if declaration.reduction is Reduction.COUNT and declaration.source is None:
                continue
            if declaration.reduction is Reduction.DERIVE and declaration.weighted_by is None:
                continue
            assert (declaration.source or figure) in fields_of_row, (name, figure)

    def test_a_model_folded_only_in_parts_names_the_rest(self):
        parts: Dict[type, list] = {}
        for table in FIGURE_TABLES.values():
            if table.served_as is not None:
                parts.setdefault(table.served_as, []).append(table)
        only_parts = {model for model, tables in parts.items()
                      if not any(table.serves_whole for table in tables)}
        assert only_parts == set(SERVED_UNFOLDED)
        for model in only_parts:
            fields_of_model = _fields_of(model)
            folded = {f for table in parts[model] for f in table.figures} & fields_of_model
            named = set(SERVED_UNFOLDED[model])
            assert not folded & named, (model.__name__, sorted(folded & named))
            assert fields_of_model == folded | named, (
                model.__name__, sorted(fields_of_model - folded - named))

    @pytest.mark.parametrize('name', sorted(_SERVED))
    def test_the_served_model_holds_the_figures_and_nothing_undeclared(self, name):
        # Both ways: a model field nobody folds or names, and a figure nobody serves or names,
        # are each a field whose answer lives nowhere.
        table = FIGURE_TABLES[name]
        fields = _fields_of(table.served_as)
        if not table.serves_whole:
            # A part names only what it folds and the model does not serve; the whole names the rest
            assert set(table.figures) - fields == set(table.outside_the_fold), name
            return
        one_side_only = fields ^ set(table.figures)
        assert one_side_only == set(table.outside_the_fold), (
            f'{name}: undeclared {sorted(one_side_only - set(table.outside_the_fold))}, '
            f'declared but on both sides or neither {sorted(set(table.outside_the_fold) - one_side_only)}')

    @pytest.mark.parametrize('name', sorted(_SERVED))
    def test_a_figure_that_can_come_back_empty_is_served_as_optional(self, name):
        table = FIGURE_TABLES[name]
        hints = get_type_hints(table.served_as)
        for figure in table.figures:
            if figure in _fields_of(table.served_as) and _can_answer_empty(table, figure):
                assert _may_be_none(hints[figure]), (name, figure, hints[figure])


class TestTheFoldAppliesTheDeclaration:
    """The fold reads, adds and fills a figure the way its declaration says."""

    def test_a_named_function_is_the_source(self):
        (combined,) = fold_rows([_Row('g', 'a', -5.0), _Row('g', 'a', 3.0)], _TABLE)
        assert combined['biggest'] == 5.0

    def test_the_two_additions_keep_their_own_last_bit(self):
        rows = [_Row('g', 'a', value) for value in (0.1, 0.2, 0.3)]
        (combined,) = fold_rows(rows, _TABLE)
        assert combined['amount'] == sum([0.1, 0.2, 0.3]) == 0.6
        loop = 0.0
        for value in (0.1, 0.2, 0.3):
            loop += value
        assert combined['amount_loop'] == loop == 0.6000000000000001

    def test_an_empty_figure_answers_its_declared_value_and_a_derivation_reads_it(self):
        (combined,) = fold_rows([_Row('g', 'a', None)], _TABLE)
        assert combined['biggest'] == 0.0
        assert combined['doubled'] == 0.0
        assert combined['amount'] is None

    def test_a_companion_follows_the_row_that_won_and_fills_only_when_none_won(self):
        rows = [_Row('g', 'a', 1.0, peak=-4.0, label='deep'),
                _Row('g', 'a', 1.0, peak=2.0, label='shallow')]
        (combined,) = fold_rows(rows, _TABLE)
        assert (combined['peak'], combined['peak_label']) == (-4.0, 'deep')
        (empty,) = fold_rows([_Row('g', 'a', 1.0)], _TABLE)
        assert (empty['peak'], empty['peak_label']) == (None, '')

    def test_groups_come_back_in_the_declared_order(self):
        rows = [_Row('b', 'a', 1.0), _Row('a', 'a', 1.0), _Row('b', 'a', 1.0)]
        assert [g['group'] for g in fold_rows(rows, _TABLE, by=('group',))] == ['b', 'a']
        sorted_table = FoldTable(name='sorted', figures=_TABLE.figures,
                                 derivations=_TABLE.derivations, account_key='account',
                                 order=GroupOrder.SORTED)
        assert [g['group'] for g in fold_rows(rows, sorted_table, by=('group',))] == ['a', 'b']

    def test_no_key_folds_every_row_into_one_group_and_no_rows_into_none(self):
        assert len(fold_rows([_Row('a', 'x', 1.0), _Row('b', 'y', 1.0)], _TABLE)) == 1
        assert fold_rows([], _TABLE) == []

    def test_an_exact_mean_is_the_one_statistics_computes(self):
        # How the robustness report averaged its windows: fsum over the count, as fmean does.
        table = FoldTable(name='exact', figures={
            'amount': FigureDeclaration(Reduction.MEAN, addition=Addition.EXACT)})
        values = [0.1, 0.2, 0.3, 1e16, -1e16, 1 / 3]
        (combined,) = fold_rows([_Row('g', 'a', value) for value in values], table)
        assert combined['amount'] == statistics.fmean(values)

    def test_a_compensated_weighted_mean_is_the_quotient_of_two_sums(self):
        # The form the aggregated portfolio's latency takes: sum(value * weight) / sum(weight).
        table = FoldTable(name='weighted', figures={
            'amount': FigureDeclaration(Reduction.DERIVE, weighted_by='weight'),
            'weight': FigureDeclaration(Reduction.SUM),
        })
        rows = [_Row('g', 'a', value, weight=count)
                for value, count in ((0.1, 3), (0.2, 5), (0.3, 7), (None, 9))]
        (combined,) = fold_rows(rows, table)
        pairs = [(0.1, 3), (0.2, 5), (0.3, 7)]
        assert combined['amount'] == (sum(v * w for v, w in pairs) / sum(w for _, w in pairs))


class TestAPlanRefusesWhatItCannotApply:
    """A declaration the fold cannot apply fails when the table is resolved, not in a group."""

    def test_a_companion_of_a_computed_figure(self):
        with pytest.raises(ValueError, match='companion'):
            fold_plan_of(FoldTable(name='bad', figures={
                'total': FigureDeclaration(Reduction.SUM),
                'label': FigureDeclaration(Reduction.COMPANION, companion_of='total'),
            }))

    def test_a_derived_figure_without_a_derivation(self):
        with pytest.raises(ValueError, match='derivation'):
            fold_plan_of(FoldTable(name='bad', figures={
                'rate': FigureDeclaration(Reduction.DERIVE)}))

    def test_a_reading_of_one_account_without_an_account_field(self):
        with pytest.raises(ValueError, match='account'):
            fold_plan_of(FoldTable(name='bad', figures={
                'equity': FigureDeclaration(Reduction.LAST)}))

    def test_a_derivation_for_an_undeclared_figure(self):
        with pytest.raises(ValueError, match='undeclared'):
            fold_plan_of(FoldTable(name='bad', figures={}, derivations={'x': _share_of_peak}))
