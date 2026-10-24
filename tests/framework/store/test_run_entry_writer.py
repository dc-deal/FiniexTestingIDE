"""
The run entry writer (#621): a run's files written by member — the path from the declaration, the
write by the member's shape, a member the pipeline does not write refused. A sweep's own files are
written the same way by the sweep entry writer.
"""

import json

import pytest

from python.framework.constants.run_layout_constants import (
    ORDER_EVENTS_STREAM,
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
    STATE_FRAMES_STREAM,
    TICK_STREAM_EVENTS_STREAM,
)
from python.framework.exceptions.store_errors import EntryMemberRefusedError
from python.framework.reporting.io.artifact_specs import RUN_SUMMARY_ARTIFACT
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.store.entry_members.run_entry_members import (
    RUN_ENTRY_MEMBERS,
    RUN_ENTRY_MEMBERS_BY_ID,
)
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.store.entry_members.sweep_entry_members import SWEEP_ENTRY_MEMBERS_BY_ID
from python.framework.store.entry_members.sweep_entry_writer import SweepEntryWriter
from python.framework.types.api.report_types import RunSummary
from python.framework.types.store_member_types import (
    EntryMemberShape,
    RunEntryMemberId,
    SweepEntryMemberId,
)

_RUN_ID = '20261023_150000_cccccccc'


def _summary() -> RunSummary:
    """A run summary with nothing in it."""
    return RunSummary(run_id=_RUN_ID, currencies=[], orders_submitted=0, orders_executed=0,
                      orders_rejected=0, sl_tp_triggered=0, unit_count=1)


class TestAMemberThePipelineDoesNotWriteIsRefused:

    @pytest.mark.parametrize('run_type, member_id', [
        (RUN_TYPE_AUTOTRADER, RunEntryMemberId.SCENARIO_LOG),
        (RUN_TYPE_SIMULATION, RunEntryMemberId.STATE_FRAMES),
    ])
    def test_another_pipeline_s_member(self, tmp_path, run_type, member_id):
        with pytest.raises(EntryMemberRefusedError):
            RunEntryWriter(tmp_path, run_type).path_of(member_id)

    def test_a_member_of_another_shape(self, tmp_path):
        writer = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER)
        with pytest.raises(EntryMemberRefusedError):
            writer.write_snapshot(RunEntryMemberId.STATE_FRAMES, '{}')
        with pytest.raises(EntryMemberRefusedError):
            writer.log_path(RunEntryMemberId.TRADE_HISTORY)


class TestEachShapeIsWrittenItsWay:

    def test_a_section_is_the_bytes_the_section_writer_writes(self, tmp_path):
        written = RunEntryWriter(tmp_path / 'run', RUN_TYPE_AUTOTRADER).write_section(
            RUN_SUMMARY_ARTIFACT, _summary())
        (tmp_path / 'before').mkdir()
        before = write_artifact(_summary(), tmp_path / 'before', RUN_SUMMARY_ARTIFACT)

        assert written == tmp_path / 'run' / 'io' / RUN_SUMMARY_ARTIFACT.filename
        assert written.read_bytes() == before.read_bytes()

    def test_an_atomic_snapshot_leaves_no_temporary_file(self, tmp_path):
        writer = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER)
        path = writer.write_snapshot(RunEntryMemberId.FOOTPRINT, '{"a": 1}')
        writer.write_snapshot(RunEntryMemberId.FOOTPRINT, '{"a": 2}')

        assert json.loads(path.read_text(encoding='utf-8')) == {'a': 2}
        assert [p.name for p in path.parent.iterdir()] == [path.name]

    def test_a_table_has_its_header_then_one_row_per_record(self, tmp_path):
        path = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER).write_table(
            RunEntryMemberId.SESSION_DIAGNOSTICS, ['tick', 'signal'],
            [{'tick': 1, 'signal': 'long'}, {'tick': 2, 'signal': ''}], sink='entry_signals')

        assert path == tmp_path / 'diagnostics' / 'entry_signals.csv'
        assert path.read_bytes() == b'tick,signal\r\n1,long\r\n2,\r\n'

    def test_a_stream_s_header_names_its_member_and_declared_version(self, tmp_path):
        appender = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER).open_stream(
            RunEntryMemberId.STATE_FRAMES, _RUN_ID, logger=None, stops='nothing is recorded')
        appender.close()

        header = json.loads(appender.get_path().read_text(encoding='utf-8').splitlines()[0])
        member = next(m for m in RUN_ENTRY_MEMBERS
                      if m.member_id is RunEntryMemberId.STATE_FRAMES)
        assert (header['stream'], header['schema_version'], header['run_id']) == (
            'state_frames', member.schema_version, _RUN_ID)

    def test_a_log_path_is_the_declared_one(self, tmp_path):
        path = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER).log_path(
            RunEntryMemberId.SESSION_DAY_LOG, day='20261023')

        assert path.relative_to(tmp_path).as_posix() == (
            'session_logs/autotrader_session_20261023.log')


class TestASweepWritesItsOwnFiles:

    def test_the_ranking_is_its_declared_table(self, tmp_path):
        path = SweepEntryWriter(tmp_path).write_table(
            SweepEntryMemberId.RANKING, ['run_id', 'score'], [{'run_id': 'r1', 'score': 1.5}])

        assert SWEEP_ENTRY_MEMBERS_BY_ID[SweepEntryMemberId.RANKING].matches(
            path.relative_to(tmp_path).as_posix())
        assert path.read_bytes() == b'run_id,score\r\nr1,1.5\r\n'

    def test_the_mount_build_log_is_its_declared_log(self, tmp_path):
        path = SweepEntryWriter(tmp_path).log_path(SweepEntryMemberId.MOUNT_BUILD_LOG)

        assert SWEEP_ENTRY_MEMBERS_BY_ID[SweepEntryMemberId.MOUNT_BUILD_LOG].matches(
            path.relative_to(tmp_path).as_posix())

    def test_a_member_of_another_shape_is_refused(self, tmp_path):
        with pytest.raises(EntryMemberRefusedError):
            SweepEntryWriter(tmp_path).log_path(SweepEntryMemberId.RANKING)


def test_a_run_stream_is_named_for_its_member():
    """A stream's header names it by its member id — the names the streams' readers expect."""
    for stream in (ORDER_EVENTS_STREAM, STATE_FRAMES_STREAM, TICK_STREAM_EVENTS_STREAM):
        assert RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId(stream)].shape is EntryMemberShape.STREAM
