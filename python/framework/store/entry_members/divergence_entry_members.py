"""
FiniexTestingIDE - Divergence Entry Members (#512)
What a divergence comparison's directory holds: the scenario set it generated for each segment, the
run index it read a copy's sessions through, and its report as JSON and as text. Its backtests are
runs of their own, declared by the run store.
"""

from typing import Tuple

from python.framework.constants.divergence_report_constants import (
    DIVERGENCE_LIVE_INDEX_FILE,
    DIVERGENCE_REPORT_FILE,
    DIVERGENCE_REPORT_TEXT_FILE,
)
from python.framework.constants.run_layout_constants import RUN_TYPE_SIMULATION
from python.framework.types.store_member_types import (
    DivergenceEntryMemberId,
    EntryMember,
    EntryMemberMoment,
    EntryMemberShape,
    EntryMemberWrite,
    EntryMemberWriteMode,
)

_DOC_DIVERGENCE = ('docs/architecture/data_storage_layout.md'
                   '#a-divergence-comparison-keeps-its-own-record')

# The comparison's command writes every member; it runs beside the simulation pipeline
_BY_THE_COMMAND = EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.ON_DEMAND,
                                   'the divergence report command ran')

DIVERGENCE_ENTRY_MEMBERS: Tuple[EntryMember, ...] = (
    EntryMember(
        DivergenceEntryMemberId.SCENARIO_SET, 'divergence_{name}.json',
        EntryMemberShape.SNAPSHOT,
        "a segment's backtest, built from what its sessions recorded",
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.ON_DEMAND,
                          'a segment reached its backtest'),),
        EntryMemberWriteMode.IN_PLACE, _DOC_DIVERGENCE),
    EntryMember(
        DivergenceEntryMemberId.LIVE_INDEX, DIVERGENCE_LIVE_INDEX_FILE, EntryMemberShape.SNAPSHOT,
        "the run index of a copy of the bot host's records, its directories moved into the copy",
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.ON_DEMAND,
                          'the sessions were read from a copy'),),
        EntryMemberWriteMode.IN_PLACE, _DOC_DIVERGENCE),
    EntryMember(
        DivergenceEntryMemberId.REPORT, DIVERGENCE_REPORT_FILE, EntryMemberShape.REPORT_OUTPUT,
        'the comparison: the live side, each segment, the measures and the verdict',
        (_BY_THE_COMMAND,), EntryMemberWriteMode.IN_PLACE, _DOC_DIVERGENCE),
    EntryMember(
        DivergenceEntryMemberId.REPORT_TEXT, DIVERGENCE_REPORT_TEXT_FILE,
        EntryMemberShape.REPORT_OUTPUT, 'the report as the command printed it',
        (_BY_THE_COMMAND,), EntryMemberWriteMode.IN_PLACE, _DOC_DIVERGENCE),
)
