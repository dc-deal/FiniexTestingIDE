"""
Two runs' records held against each other (#641), on run directories written in the test: every
value both write compared to the last digit, a field only one writes named as a change of shape,
the ignored fields never compared, and an order event stream put in scenario and `seq` order.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from python.framework.behaviour_comparison.compared_records import COMPARED_RECORDS
from python.framework.behaviour_comparison.run_record_comparison import (
    compare_record,
    compare_run_records,
    disagreeing_paths,
)
from python.framework.types.behaviour_comparison_types import ComparedRecord, RecordComparison
from python.framework.types.store_member_types import RunEntryMemberId

_ORDERS = ComparedRecord(RunEntryMemberId.ORDER_HISTORY, 'orders', records_key='orders',
                         unseen_words='an order')


def _run_dir(root: Path, files: Dict[str, Any]) -> Path:
    """
    A run directory holding the given files.

    Args:
        root: Where it is made
        files: Path inside the run → a JSON document, or a list of lines for a stream

    Returns:
        The directory
    """
    root.mkdir(parents=True)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, list):
            path.write_text(''.join(json.dumps(line) + '\n' for line in content), encoding='utf-8')
        else:
            path.write_text(json.dumps(content), encoding='utf-8')
    return root


def _by_member(comparisons: List[RecordComparison]) -> Dict[RunEntryMemberId, RecordComparison]:
    return {comparison.record.member_id: comparison for comparison in comparisons}


class TestValues:

    def test_equal_documents_have_no_difference(self):
        document = {'run_id': 'a', 'orders': [{'lots': 0.5, 'status': 'filled'}]}
        comparison = compare_record(_ORDERS, document, dict(document, run_id='b'))
        assert comparison.differences == [] and comparison.old_only_fields == []

    def test_a_differing_value_names_its_path_and_both_values(self):
        old = {'orders': [{'lots': 0.03201}, {'lots': 1.0}]}
        new = {'orders': [{'lots': 0.03199}, {'lots': 1.0}]}
        difference, = compare_record(_ORDERS, old, new).differences
        assert (difference.path, difference.folded_path) == ('orders[0].lots', 'orders[*].lots')
        assert (difference.old_value, difference.new_value) == ('0.03201', '0.03199')

    def test_an_extra_record_is_a_difference_with_no_value_on_the_other_side(self):
        old = {'orders': [{'lots': 1.0}]}
        new = {'orders': [{'lots': 1.0}, {'lots': 2.0}]}
        difference, = compare_record(_ORDERS, old, new).differences
        assert difference.path == 'orders[1]' and difference.old_value is None
        assert json.loads(difference.new_value) == {'lots': 2.0}

    def test_a_changed_type_is_a_difference(self):
        comparison = compare_record(_ORDERS, {'count': 1}, {'count': 1.0})
        assert [difference.path for difference in comparison.differences] == ['count']


class TestShape:

    def test_a_field_only_one_run_writes_is_named_not_compared(self):
        old = {'orders': [{'lots': 1.0, 'fee': 0.1}]}
        new = {'orders': [{'lots': 1.0, 'fee_basis': 'quote'}]}
        comparison = compare_record(_ORDERS, old, new)
        assert comparison.differences == []
        assert comparison.old_only_fields == ['orders[*].fee']
        assert comparison.new_only_fields == ['orders[*].fee_basis']

    def test_a_schema_version_is_a_change_of_shape(self):
        comparison = compare_record(_ORDERS, {'schema_version': 2}, {'schema_version': 3})
        assert comparison.differences == []
        assert [difference.path for difference in comparison.shape_differences] == [
            'schema_version']


class TestIgnoredFields:

    def test_the_run_id_and_a_record_s_own_ignored_fields_are_never_compared(self):
        record = ComparedRecord(RunEntryMemberId.WARNINGS_ERRORS, 'warnings and errors',
                                ignored_fields=('observed_at',))
        old = {'run_id': 'a', 'errors': [{'observed_at': '10:00', 'event_time': 't1'}]}
        new = {'run_id': 'b', 'errors': [{'observed_at': '10:05', 'event_time': 't2'}]}
        difference, = compare_record(record, old, new).differences
        assert difference.path == 'errors[0].event_time'


class TestRunDirectories:

    def test_an_order_event_stream_is_held_in_scenario_and_seq_order(self, tmp_path):
        header = {'record_kind': 'header', 'stream': 'order_events', 'schema_version': 2}
        first = {'scenario_name': 'a', 'seq': 1, 'event_type': 'submitted'}
        second = {'scenario_name': 'b', 'seq': 1, 'event_type': 'filled'}
        old = _run_dir(tmp_path / 'old', {'io/order_events.jsonl': [
            dict(header, run_id='a'), first, second]})
        new = _run_dir(tmp_path / 'new', {'io/order_events.jsonl': [
            dict(header, run_id='b'), second, first]})
        events = _by_member(compare_run_records(old, new))[RunEntryMemberId.ORDER_EVENTS]
        assert events.present_in_old and events.differences == []

    def test_a_kind_of_record_neither_run_wrote_is_absent_in_both(self, tmp_path):
        old = _run_dir(tmp_path / 'old', {})
        new = _run_dir(tmp_path / 'new', {})
        robustness = _by_member(compare_run_records(old, new))[RunEntryMemberId.ROBUSTNESS]
        assert not robustness.present_in_old and not robustness.present_in_new
        assert not robustness.empty_in_both

    def test_an_empty_records_list_in_both_runs_says_nothing_happened(self, tmp_path):
        old = _run_dir(tmp_path / 'old', {'io/trade_history.json': {'trades': []}})
        new = _run_dir(tmp_path / 'new', {'io/trade_history.json': {'trades': []}})
        trades = _by_member(compare_run_records(old, new))[RunEntryMemberId.TRADE_HISTORY]
        assert trades.empty_in_both

    def test_a_member_with_a_variable_name_is_compared_file_by_file(self, tmp_path):
        frame = {'frame_time': 't', 'written_at': 'w1', 'equity': 100.0}
        old = _run_dir(tmp_path / 'old', {'io/state_frames_s1.jsonl': [frame]})
        new = _run_dir(tmp_path / 'new', {'io/state_frames_s1.jsonl': [
            dict(frame, written_at='w2', equity=101.0)]})
        frames = _by_member(compare_run_records(old, new))[RunEntryMemberId.SCENARIO_STATE_FRAMES]
        difference, = frames.differences
        assert difference.path == 'io/state_frames_s1.jsonl.records[0].equity'

    def test_every_declared_kind_of_record_is_compared(self, tmp_path):
        old = _run_dir(tmp_path / 'old', {})
        new = _run_dir(tmp_path / 'new', {})
        compared = [comparison.record for comparison in compare_run_records(old, new)]
        assert compared == list(COMPARED_RECORDS)


class TestDisagreeingPaths:

    def test_the_paths_two_runs_disagree_on_are_listed_by_kind_of_record(self):
        comparison = compare_record(_ORDERS, {'orders': [{'lots': 1.0}]},
                                    {'orders': [{'lots': 2.0, 'x': 1}]})
        assert disagreeing_paths([comparison]) == {
            'order_history': ['orders[*].lots', 'orders[*].x']}

    def test_a_kind_of_record_they_agree_on_is_left_out(self):
        comparison = compare_record(_ORDERS, {'orders': []}, {'orders': []})
        assert disagreeing_paths([comparison]) == {}
