"""
FiniexTestingIDE - Sweep Entry Members (#621)
What a sweep directory holds beside its combinations: the log of the data load all of them share,
and the ranking the report command writes. Each combination is a run of its own, declared by the
run store.
"""

from typing import Dict, Tuple

from python.framework.types.log_layout_types import (
    MOUNT_BUILD_LOG,
    RUN_TYPE_SIMULATION,
    SWEEP_RANKING_FILE,
)
from python.framework.types.store_member_types import (
    EntryMember,
    EntryMemberMoment,
    EntryMemberShape,
    EntryMemberWrite,
    EntryMemberWriteMode,
    SweepEntryMemberId,
)

_DOC_SWEEP = 'docs/architecture/parameter_optimization_system.md#what-a-sweep-directory-holds'

SWEEP_ENTRY_MEMBERS: Tuple[EntryMember, ...] = (
    EntryMember(
        SweepEntryMemberId.MOUNT_BUILD_LOG, MOUNT_BUILD_LOG, EntryMemberShape.LOG,
        'the data load every combination reuses, performed once',
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.RUN_START,
                          'mount reuse is switched on'),),
        EntryMemberWriteMode.APPEND_LINES, _DOC_SWEEP),
    EntryMember(
        SweepEntryMemberId.RANKING, SWEEP_RANKING_FILE, EntryMemberShape.REPORT_OUTPUT,
        'the combinations ranked by the objective',
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.ON_DEMAND,
                          'the report command ran'),),
        EntryMemberWriteMode.IN_PLACE, _DOC_SWEEP),
)

SWEEP_ENTRY_MEMBERS_BY_ID: Dict[SweepEntryMemberId, EntryMember] = {
    member.member_id: member for member in SWEEP_ENTRY_MEMBERS}
