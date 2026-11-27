"""
The result of a behaviour comparison (#641), judged from comparisons built in the test: a verdict
for each kind of record and for the whole, what the new stand's second run leaves unjudged, and the
lines the console prints.
"""

from typing import List

from python.framework.behaviour_comparison.run_record_comparison import compare_record
from python.framework.reporting.console.behaviour_comparison_console import (
    render_behaviour_comparison,
)
from python.framework.types.behaviour_comparison_types import (
    BehaviourComparison,
    ComparedRecord,
    ComparisonEnding,
    ComparisonStep,
    ComparisonVerdict,
    RecordComparison,
    StandCommits,
)
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.validators.behaviour_comparison_validator import judge_result

_ORDERS = ComparedRecord(RunEntryMemberId.ORDER_HISTORY, 'orders', records_key='orders',
                         unseen_words='an order')
_TRADES = ComparedRecord(RunEntryMemberId.TRADE_HISTORY, 'trades', records_key='trades',
                         unseen_words='a closed position')
_PORTFOLIO = ComparedRecord(RunEntryMemberId.PORTFOLIO, 'positions and the account')


def _orders(*lots: float) -> dict:
    return {'orders': [{'lots': value} for value in lots]}


def _verdicts(records: List[RecordComparison],
              second_run: List[RecordComparison] = ()) -> List[tuple]:
    _, lines = judge_result(records, list(second_run))
    return [(line.verdict, line.words) for line in lines]


class TestEachKindOfRecord:

    def test_equal_records_are_identical(self):
        records = [compare_record(_ORDERS, _orders(1.0), _orders(1.0))]
        assert judge_result(records, [])[0] is ComparisonVerdict.IDENTICAL
        assert _verdicts(records) == [(ComparisonVerdict.IDENTICAL, 'orders')]

    def test_a_differing_value_is_different_and_counted_by_field(self):
        records = [compare_record(_ORDERS, _orders(1.0, 2.0, 3.0), _orders(1.0, 2.5, 3.5))]
        whole, lines = judge_result(records, [])
        assert whole is ComparisonVerdict.DIFFERENT
        field, = lines[0].differing_fields
        assert (field.folded_path, field.count, field.first.path) == (
            'orders[*].lots', 2, 'orders[1].lots')

    def test_a_field_only_one_stand_writes_reshapes_the_record(self):
        records = [compare_record(_ORDERS, {'orders': [{'lots': 1.0}]},
                                  {'orders': [{'lots': 1.0, 'fee_basis': 'quote'}]})]
        whole, lines = judge_result(records, [])
        assert whole is ComparisonVerdict.RESHAPED
        assert lines[0].reshaped == ('a field only the new stand writes — orders[*].fee_basis',)

    def test_a_value_and_a_shape_both_moved_earn_both_lines(self):
        records = [compare_record(_ORDERS, {'orders': [{'lots': 1.0}]},
                                  {'orders': [{'lots': 2.0, 'fee_basis': 'quote'}]})]
        assert _verdicts(records) == [(ComparisonVerdict.DIFFERENT, 'orders'),
                                      (ComparisonVerdict.RESHAPED, 'orders')]

    def test_a_record_only_one_stand_writes_reshapes(self):
        records = [compare_record(_PORTFOLIO, None, {'units': []})]
        _, lines = judge_result(records, [])
        assert lines[0].reshaped == ('only the new stand writes it',)

    def test_nothing_of_a_kind_in_either_run_is_unseen(self):
        records = [compare_record(_TRADES, {'trades': []}, {'trades': []})]
        whole, lines = judge_result(records, [])
        assert whole is ComparisonVerdict.UNSEEN
        assert lines[0].unseen_because == ('none in either run — says nothing about a closed '
                                           'position')

    def test_a_kind_of_record_neither_run_wrote_earns_no_line(self):
        assert _verdicts([compare_record(_PORTFOLIO, None, None)]) == []


class TestTheSecondRun:

    def test_a_value_the_new_stand_s_two_runs_disagree_on_is_not_judged(self):
        records = [compare_record(_ORDERS, _orders(1.0), _orders(2.0))]
        second = [compare_record(_ORDERS, _orders(2.0), _orders(2.1))]
        whole, lines = judge_result(records, second)
        assert whole is ComparisonVerdict.UNSEEN
        line, = lines
        assert line.verdict is ComparisonVerdict.UNSEEN
        assert line.differing_fields[0].folded_path == 'orders[*].lots'

    def test_a_second_run_that_agrees_leaves_the_difference_judged(self):
        records = [compare_record(_ORDERS, _orders(1.0), _orders(2.0))]
        second = [compare_record(_ORDERS, _orders(2.0), _orders(2.0))]
        assert judge_result(records, second)[0] is ComparisonVerdict.DIFFERENT


class TestTheWhole:

    def test_different_outweighs_reshaped_and_identical_and_comes_first(self):
        records = [compare_record(_PORTFOLIO, {'units': []}, {'units': [], 'x': 1}),
                   compare_record(_TRADES, {'trades': [1]}, {'trades': [1]}),
                   compare_record(_ORDERS, _orders(1.0), _orders(2.0))]
        whole, lines = judge_result(records, [])
        assert whole is ComparisonVerdict.DIFFERENT
        assert [line.verdict for line in lines] == [ComparisonVerdict.DIFFERENT,
                                                    ComparisonVerdict.RESHAPED,
                                                    ComparisonVerdict.IDENTICAL]

    def test_the_verdicts_rank_as_declared(self):
        assert ComparisonVerdict.DIFFERENT.ranks_above(ComparisonVerdict.RESHAPED)
        assert ComparisonVerdict.RESHAPED.ranks_above(ComparisonVerdict.IDENTICAL)
        assert ComparisonVerdict.IDENTICAL.ranks_above(ComparisonVerdict.UNSEEN)
        assert all(verdict.summary() for verdict in ComparisonVerdict)


class TestTheConsole:

    def test_the_result_lines_name_the_field_and_its_first_values(self):
        records = [compare_record(_ORDERS, _orders(0.03201), _orders(0.03199)),
                   compare_record(_TRADES, {'trades': []}, {'trades': []})]
        whole, lines = judge_result(records, [])
        comparison = BehaviourComparison(
            comparison_id='20261127_120000', scenario_set='my_check_set.json',
            old=StandCommits('a' * 40), new=StandCommits('b' * 40), records=records,
            result_lines=lines, verdict=whole, ended_in_step=ComparisonStep.RESULT,
            ending=ComparisonEnding.COMPARED, ended_because=whole.summary(), cleaned_up=True)
        printed = render_behaviour_comparison(comparison)
        assert '6 result  DIFFERENT  orders' in printed
        assert 'orders[*].lots ×1 — first orders[0].lots 0.03201 → 0.03199' in printed
        assert 'UNSEEN     trades: none in either run' in printed
        assert 'ended in result: the behaviour differs' in printed


class TestEverythingTheSecondRunDisagreesOn:

    def test_a_path_the_second_run_disagrees_on_is_named_where_old_and_new_agree(self):
        records = [compare_record(_ORDERS, _orders(1.0), _orders(1.0))]
        second = [compare_record(_ORDERS, _orders(1.0), _orders(1.1))]
        _, lines = judge_result(records, second)
        assert [line.verdict for line in lines] == [ComparisonVerdict.IDENTICAL,
                                                    ComparisonVerdict.UNSEEN]
        assert lines[1].differing_fields[0].first.new_value == '1.1'

    def test_a_field_only_one_of_its_two_runs_writes_is_named_and_not_a_reshape(self):
        records = [compare_record(_ORDERS, {'orders': [{'lots': 1.0}]},
                                  {'orders': [{'lots': 1.0, 'stamp': 'a'}]})]
        second = [compare_record(_ORDERS, {'orders': [{'lots': 1.0, 'stamp': 'a'}]},
                                 {'orders': [{'lots': 1.0}]})]
        whole, lines = judge_result(records, second)
        assert whole is ComparisonVerdict.IDENTICAL
        unseen, = [line for line in lines if line.verdict is ComparisonVerdict.UNSEEN]
        assert unseen.reshaped == ('a field only one of its two runs writes — orders[*].stamp',)
