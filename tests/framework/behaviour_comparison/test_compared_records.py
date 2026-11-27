"""
Which records of a backtest the behaviour comparison holds against each other (#641): every entry
member a backtest writes is compared or left out with its reason, and nothing else is named.
"""

from python.framework.behaviour_comparison.compared_records import (
    COMPARED_RECORDS,
    NOT_COMPARED_RECORDS,
)
from python.framework.constants.run_layout_constants import RUN_TYPE_SIMULATION
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS
from python.framework.types.store_member_types import EntryMemberShape

_BACKTEST_MEMBERS = {member.member_id for member in RUN_ENTRY_MEMBERS
                     if any(write.run_type == RUN_TYPE_SIMULATION for write in member.written_by)}
_COMPARED = {record.member_id for record in COMPARED_RECORDS}


class TestTheDeclaration:

    def test_every_member_a_backtest_writes_is_compared_or_left_out_with_a_reason(self):
        assert _BACKTEST_MEMBERS, 'the entry-member declaration names no backtest member'
        assert sorted(_BACKTEST_MEMBERS - _COMPARED - set(NOT_COMPARED_RECORDS)) == []

    def test_every_named_member_is_one_a_backtest_writes(self):
        assert sorted((_COMPARED | set(NOT_COMPARED_RECORDS)) - _BACKTEST_MEMBERS) == []

    def test_no_member_is_both_compared_and_left_out(self):
        assert sorted(_COMPARED & set(NOT_COMPARED_RECORDS)) == []
        assert len(_COMPARED) == len(COMPARED_RECORDS)

    def test_every_reason_and_every_name_is_written_out(self):
        assert all(reason.strip() for reason in NOT_COMPARED_RECORDS.values())
        assert all(record.words.strip() for record in COMPARED_RECORDS)
        assert all(record.unseen_words.strip() for record in COMPARED_RECORDS
                   if record.records_key is not None)

    def test_no_log_is_compared(self):
        shapes = {member.member_id: member.shape for member in RUN_ENTRY_MEMBERS}
        assert [member for member in _COMPARED if shapes[member] is EntryMemberShape.LOG] == []
