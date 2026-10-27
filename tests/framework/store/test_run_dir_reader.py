"""
The run directory reader (#621): one run directory's files by member, for a caller that already
holds the directory. Paths come from the declaration; what a file holds is read by its own reader,
with that reader's errors.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.reporting.io.artifact_specs import RUN_SUMMARY_ARTIFACT
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.entry_members.run_entry_members import (
    RUN_ENTRY_MEMBERS_BY_ID,
    run_member_file_name,
    run_member_folder,
    run_member_path,
)
from python.framework.types.api.report_types import RunHeader, RunSummary
from python.framework.types.store_member_types import RunEntryMemberId

_RUN_ID = '20261024_120000_eeeeeeee'
_START = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _session_dir(tmp_path):
    """A session's run directory holding its header."""
    write_run_header(RunHeader(run_id=_RUN_ID, start_time=_START, run_type=RUN_TYPE_AUTOTRADER,
                               run_name='my_bot'), tmp_path)
    return tmp_path


class TestThePathsAreTheDeclaredOnes:

    def test_a_member_path_folder_and_name(self):
        path = run_member_path(RunEntryMemberId.STATE_FRAMES)

        assert RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.STATE_FRAMES].matches(path)
        assert f'{run_member_folder(RunEntryMemberId.STATE_FRAMES)}/' \
               f'{run_member_file_name(RunEntryMemberId.STATE_FRAMES)}' == path

    def test_a_member_in_the_run_directory_itself_has_no_folder(self):
        assert run_member_folder(RunEntryMemberId.HEADER) == ''
        assert run_member_file_name(RunEntryMemberId.HEADER) == run_member_path(
            RunEntryMemberId.HEADER)

    def test_the_reader_places_a_member_where_the_declaration_says(self, tmp_path):
        assert RunDirReader(tmp_path).path_of(RunEntryMemberId.SESSION_DAY_LOG, day='20261024') \
            == tmp_path / run_member_path(RunEntryMemberId.SESSION_DAY_LOG, day='20261024')


class TestWhatAFileHoldsIsReadByItsOwnReader:

    def test_the_header(self, tmp_path):
        assert RunDirReader(_session_dir(tmp_path)).read_header().run_id == _RUN_ID

    def test_a_missing_header_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            RunDirReader(tmp_path).read_header()

    def test_a_section_is_none_until_written_and_then_read(self, tmp_path):
        reader = RunDirReader(_session_dir(tmp_path))
        assert reader.read_section(RUN_SUMMARY_ARTIFACT) is None

        summary = RunSummary(run_id=_RUN_ID, currencies=[], orders_submitted=0,
                             orders_executed=0, orders_rejected=0, sl_tp_triggered=0,
                             unit_count=1)
        folder = reader.section_path(RUN_SUMMARY_ARTIFACT).parent
        folder.mkdir()
        write_artifact(summary, folder, RUN_SUMMARY_ARTIFACT)

        assert reader.read_section(RUN_SUMMARY_ARTIFACT).run_id == _RUN_ID

    def test_a_section_that_no_longer_fits_raises_the_validation_error(self, tmp_path):
        reader = RunDirReader(tmp_path)
        path = reader.section_path(RUN_SUMMARY_ARTIFACT)
        path.parent.mkdir()
        path.write_text('{"run_id": 1}', encoding='utf-8')

        with pytest.raises(ValidationError):
            reader.read_section(RUN_SUMMARY_ARTIFACT)

    def test_a_stream_reads_only_when_it_is_there(self, tmp_path):
        reader = RunDirReader(tmp_path)
        assert reader.read_stream(RunEntryMemberId.ORDER_EVENTS) is None
        assert reader.read_stream_ends(RunEntryMemberId.ORDER_EVENTS) is None

        path = reader.path_of(RunEntryMemberId.ORDER_EVENTS)
        path.parent.mkdir()
        path.write_text('', encoding='utf-8')

        assert reader.read_stream(RunEntryMemberId.ORDER_EVENTS).records == []
        assert reader.has(RunEntryMemberId.ORDER_EVENTS)


class TestTheFilesOfAPatternMember:

    def test_only_the_files_that_are_the_member(self, tmp_path):
        reader = RunDirReader(tmp_path)
        days = [reader.path_of(RunEntryMemberId.SESSION_DAY_LOG, day=day)
                for day in ('20261023', '20261024')]
        days[0].parent.mkdir()
        for path in days:
            path.write_text('log\n', encoding='utf-8')
        (days[0].parent / 'notes.txt').write_text('not a day log', encoding='utf-8')

        assert reader.files_of(RunEntryMemberId.SESSION_DAY_LOG) == days

    def test_none_when_the_folder_is_missing(self, tmp_path):
        assert RunDirReader(tmp_path).files_of(RunEntryMemberId.SESSION_DAY_LOG) == []

    def test_a_member_whose_folder_is_a_variable_part_is_found(self, tmp_path):
        """A day record lies in its day's own folder (#476): the walk starts above it, in days/."""
        reader = RunDirReader(tmp_path)
        days = [reader.path_of(RunEntryMemberId.TRADES_DAY, day=day)
                for day in ('20261102', '20261103')]
        for path in days:
            path.parent.mkdir(parents=True)
            path.write_bytes(b'not read here')
        state = reader.path_of(RunEntryMemberId.STATE_FRAMES_DAY, day='20261102')
        state.write_bytes(b'another member')

        assert reader.files_of(RunEntryMemberId.TRADES_DAY) == days
