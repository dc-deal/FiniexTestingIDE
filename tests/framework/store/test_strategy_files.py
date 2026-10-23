"""
A strategy's own files (#621): written at once into the run's declared folder, never a path in the
strategy's hand, never the end of a run — and counted by the footprint, not listed.
"""

import json
from datetime import datetime, timezone

import pytest

from python.framework.constants.run_layout_constants import (
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
)
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.store.entry_members.run_footprint import (
    assess,
    read_footprint,
    write_footprint,
)
from python.framework.store.entry_members.strategy_file import StrategyFile
from python.framework.store.entry_members.strategy_file_writer import StrategyFileWriter
from python.framework.types.api.report_types import RunHeader
from python.framework.types.run_footprint_types import MemberFootprintState
from python.framework.types.store_member_types import RunEntryMemberId
from tests.shared.recording_logger import RecordingLogger

_RUN_ID = '20261023_160000_dddddddd'


def _session_files(tmp_path, logger=None) -> StrategyFileWriter:
    """A session's strategy file writer over a run directory in tmp_path."""
    return StrategyFileWriter(RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER),
                              RunEntryMemberId.SESSION_STRATEGY_FILE, logger or RecordingLogger())


class TestAFileLandsWhereTheDeclarationSays:

    def test_a_session_s_file_is_in_its_folder_and_replaced_whole(self, tmp_path):
        files = _session_files(tmp_path)

        assert files.write_json('last_setup.json', {'gate': 'up'})
        assert files.write_json('last_setup.json', {'gate': 'down'})

        folder = tmp_path / 'strategy_files'
        assert json.loads((folder / 'last_setup.json').read_text(encoding='utf-8')) == {
            'gate': 'down'}
        assert [path.name for path in folder.iterdir()] == ['last_setup.json']

    def test_a_scenario_s_file_is_in_its_scenario_s_folder(self, tmp_path):
        files = StrategyFileWriter(
            RunEntryWriter(tmp_path, RUN_TYPE_SIMULATION), RunEntryMemberId.SCENARIO_STRATEGY_FILE,
            RecordingLogger(), scenario='window_01')

        assert files.write_text('notes.txt', 'seen')

        assert (tmp_path / 'strategy_files' / 'window_01' / 'notes.txt').read_text(
            encoding='utf-8') == 'seen'


class TestAFailedWriteNeverEndsTheRun:

    @pytest.mark.parametrize('name', ['../header.json', 'a/b.json', '.hidden', 'channels.tmp',
                                      '', '..'])
    def test_a_name_that_reaches_out_or_takes_a_temporary_name_is_refused(self, tmp_path, name):
        logger = RecordingLogger()
        files = _session_files(tmp_path, logger)

        assert files.write_text(name, 'x') is False
        assert files.write_text(name, 'x') is False

        assert not (tmp_path / 'strategy_files').exists()
        assert len([line for line in logger.lines if line[0] == 'error']) == 1

    def test_a_disk_that_refuses_is_said_once(self, tmp_path):
        (tmp_path / 'strategy_files').write_text('a file where the folder should be')
        logger = RecordingLogger()
        files = _session_files(tmp_path, logger)

        assert files.write_text('a.json', '{}') is False
        assert files.write_text('b.json', '{}') is False

        assert len([line for line in logger.lines if line[0] == 'error']) == 1

    def test_content_that_is_no_json_is_said_and_not_written(self, tmp_path):
        logger = RecordingLogger()

        assert _session_files(tmp_path, logger).write_json('a.json', {'at': datetime.now(
            timezone.utc)}) is False

        assert not (tmp_path / 'strategy_files').exists()
        assert logger.lines[0][0] == 'error'

    def test_a_run_without_a_directory_writes_nothing_and_says_so_once(self):
        logger = RecordingLogger()
        files = StrategyFileWriter(None, RunEntryMemberId.SESSION_STRATEGY_FILE, logger)

        assert files.write_text('a.json', '{}') is False
        assert files.write_text('b.json', '{}') is False

        assert len(logger.lines) == 1

    def test_a_logic_the_framework_wired_nothing_into_writes_nothing(self):
        assert StrategyFile('a.json', None).write_json({}) is False


class TestTheFootprintCountsThem:

    def _closed_session(self, tmp_path, file_names) -> None:
        """A session run directory whose strategy wrote the given files, then closed."""
        write_run_header(RunHeader(run_id=_RUN_ID, start_time=datetime.now(timezone.utc),
                                   run_type=RUN_TYPE_AUTOTRADER, run_name='my_bot'), tmp_path)
        files = _session_files(tmp_path)
        for name in file_names:
            files.write_text(name, 'x' * 10)
        write_footprint(tmp_path, _RUN_ID, RUN_TYPE_AUTOTRADER, [])

    def _state(self, tmp_path) -> MemberFootprintState:
        return next(member.state for member in assess(tmp_path).members
                    if member.member_id is RunEntryMemberId.SESSION_STRATEGY_FILE)

    def test_counted_not_listed(self, tmp_path):
        self._closed_session(tmp_path, ['a.json', 'b.json'])

        record = read_footprint(tmp_path)
        assert [(folder.file_count, folder.size_bytes) for folder in record.folders] == [(2, 20)]
        assert not [entry for entry in record.files
                    if entry.member_id is RunEntryMemberId.SESSION_STRATEGY_FILE]
        assert self._state(tmp_path) is MemberFootprintState.PRESENT

    def test_none_written_is_skipped_never_failed(self, tmp_path):
        self._closed_session(tmp_path, [])

        assert self._state(tmp_path) is MemberFootprintState.SKIPPED

    def test_one_gone_since_the_close_is_missing_and_one_more_is_late(self, tmp_path):
        self._closed_session(tmp_path, ['a.json', 'b.json'])
        (tmp_path / 'strategy_files' / 'a.json').unlink()
        assert self._state(tmp_path) is MemberFootprintState.MISSING

        _session_files(tmp_path).write_text('a.json', 'x')
        _session_files(tmp_path).write_text('c.json', 'x')
        assert self._state(tmp_path) is MemberFootprintState.LATE
