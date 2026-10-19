"""
Which process counts toward which activity (#603): each process once, toward the nearest activity
above it, and an activity nested in its parent's process holds nothing of its own.
"""

from datetime import datetime, timedelta, timezone

from python.framework.activities.activity_memory_attribution import attribute_memory
from python.framework.types.activity_registry_types import (
    ActivityEntry,
    ActivityKind,
    ActivityState,
)
from python.framework.types.process_table_types import ProcessReading

_T0 = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)
_MB = 1024 ** 2


def _entry(activity_id, pid, parent=None, minutes=0):
    return ActivityEntry(
        activity_id=activity_id, activity_kind=ActivityKind.BACKTEST,
        activity_state=ActivityState.RUNNING, activity_label=activity_id, pid=pid,
        process_started_at=0.0, command_line=[], started_at=_T0 + timedelta(minutes=minutes),
        started_monotonic=0.0, working_tree='/tree', parent_activity_id=parent)


def _table(*rows):
    return {pid: ProcessReading(pid=pid, ppid=ppid, rss_bytes=rss) for pid, ppid, rss in rows}


class TestEveryProcessCountsOnce:

    def test_an_activity_holds_its_process_and_its_children(self):
        table = _table((1, 0, 5 * _MB), (100, 1, 100 * _MB), (101, 100, 50 * _MB),
                       (102, 101, 25 * _MB), (200, 1, 70 * _MB))
        memory = attribute_memory([_entry('a', 100)], table)
        assert (memory['a'].rss_bytes, memory['a'].process_count) == (175 * _MB, 3)

    def test_a_child_activity_in_its_own_process_takes_that_process(self):
        table = _table((100, 1, 100 * _MB), (300, 100, 40 * _MB), (301, 300, 10 * _MB))
        memory = attribute_memory([_entry('suite', 100), _entry('job', 300, minutes=1)], table)
        assert (memory['suite'].rss_bytes, memory['suite'].process_count) == (100 * _MB, 1)
        assert (memory['job'].rss_bytes, memory['job'].process_count) == (50 * _MB, 2)

    def test_an_activity_nested_in_its_parents_process_holds_nothing_of_its_own(self):
        table = _table((100, 1, 100 * _MB), (101, 100, 50 * _MB))
        memory = attribute_memory([_entry('sweep', 100), _entry('combo', 100, parent='sweep',
                                                                   minutes=1)], table)
        assert (memory['sweep'].rss_bytes, memory['sweep'].process_count) == (150 * _MB, 2)
        assert (memory['combo'].rss_bytes, memory['combo'].process_count) == (0, 0)


class TestWhatCannotBeReadIsNoneNeverZero:

    def test_an_activity_whose_process_is_gone_is_none(self):
        memory = attribute_memory([_entry('gone', 999)], _table((1, 0, _MB)))
        assert (memory['gone'].rss_bytes, memory['gone'].process_count) == (None, 0)

    def test_one_unreadable_process_makes_the_sum_none(self):
        table = _table((100, 1, 100 * _MB), (101, 100, None))
        memory = attribute_memory([_entry('a', 100)], table)
        assert (memory['a'].rss_bytes, memory['a'].process_count) == (None, 2)
