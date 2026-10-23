"""
The run entry census (#621): what real runs write, held to what the run store declares.

The declaration test reads the declaration alone; this suite runs a sweep and a mock session and
reads the directories they leave. Two directions, and both can fail. No file without a member — a
writer that adds a file nobody declared is caught here. And every member written somewhere — a
member that no run produces any more, or a writer that moved its file, is caught here too.
"""

import json
import shutil
from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.constants.run_layout_constants import IO_SUBDIR, RUN_TYPE_SIMULATION
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.entry_members.run_entry_members import (
    RUN_ENTRY_MEMBERS,
    RUN_ENTRY_MEMBERS_BY_ID,
    RUN_INDEX_COLUMN_MEMBERS,
)
from python.framework.types.store_member_types import (
    EntryMemberShape,
    RunEntryMemberId,
    RunIndexColumn,
    SweepEntryMemberId,
)
from tests.shared.entry_census_helpers import (
    members_present,
    run_files,
    run_type_of,
    stream_version_mismatches,
    sweep_members_present,
    undeclared_files,
    undeclared_sweep_files,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The members the census runs cannot produce, the test that covers each one, and how far that test
# reaches. Only the two captures are written into a real run directory there, held to the
# declaration by `assert_run_dir_declared`; for the other three no test writes the file where a run
# writes it. Kept short on purpose: a member that joins this table is one the census no longer sees.
_COVERED_ELSEWHERE = {
    RunEntryMemberId.BLOCK_SPLITTING: (
        'tests/framework/reporting/test_block_splitting_report.py',
        'written by a profile run only; its builder alone is tested, no run directory'),
    RunEntryMemberId.COLD_START: (
        'tests/autotrader/cold_start/test_position_book.py',
        'written only when a boot inherits an open position or finds a resting order; a mock '
        'session is always dry, so it never writes the book a next session could restore; its '
        'builder alone is tested, no run directory (#304)'),
    RunEntryMemberId.FIELD_STUDY_CAPTURE: (
        'tests/autotrader/integration/test_field_study_capture.py',
        'written by a field-study profile; that suite holds its run directory to the declaration'),
    RunEntryMemberId.SECURITY_CERT_CAPTURE: (
        'tests/autotrader/integration/test_security_study_stages.py',
        'written by a security-study stage; that suite holds each stage\'s run directory to the '
        'declaration'),
    RunEntryMemberId.TICK_STREAM_EVENTS: (
        'tests/autotrader/tick_sources/test_kraken_tick_stream_record.py',
        'written only by a session that reads its ticks from a venue; its recorder is tested in a '
        'temporary directory, no run directory'),
}


@pytest.fixture(scope='module')
def census_runs(census_sweep, census_session) -> list:
    """Every run directory the census made."""
    return census_sweep.run_dirs + [census_session]


class TestNoFileWithoutAMember:
    """Direction one: everything a run writes is declared for its pipeline."""

    def test_the_census_made_a_run_of_each_pipeline(self, census_sweep, census_session):
        assert census_sweep.run_dirs, 'the sweep made no combination'
        assert census_session.is_dir()

    def test_every_file_of_every_run_is_declared(self, census_runs):
        for run_dir in census_runs:
            assert undeclared_files(run_dir) == [], run_dir

    def test_every_file_of_the_sweep_is_declared(self, census_sweep):
        assert undeclared_sweep_files(census_sweep.sweep_dir) == []

    def test_every_sweep_member_is_written(self, census_sweep):
        assert sweep_members_present(census_sweep.sweep_dir) == set(SweepEntryMemberId)


class TestEveryMemberIsWrittenSomewhere:
    """Direction two: no member is declared for a file that no run writes any more."""

    def test_every_member_is_seen_or_covered_by_a_named_test(self, census_runs):
        seen = set()
        for run_dir in census_runs:
            seen |= set(members_present(run_dir))
        missing = set(RunEntryMemberId) - seen - set(_COVERED_ELSEWHERE)
        assert not missing, f'declared, but no census run wrote them: {sorted(missing)}'

    def test_every_unconditional_write_is_seen_in_its_own_pipeline(self, census_runs):
        """
        A member both pipelines always write, dropped by one of them, would still be seen in the
        other's runs: what each pipeline always writes is counted in that pipeline's runs alone.
        """
        seen_by_pipeline = {}
        for run_dir in census_runs:
            seen = seen_by_pipeline.setdefault(run_type_of(run_dir), set())
            seen.update(members_present(run_dir))
        missing = [(member.member_id.value, write.run_type)
                   for member in RUN_ENTRY_MEMBERS if member.member_id not in _COVERED_ELSEWHERE
                   for write in member.written_by
                   if not write.condition
                   and member.member_id not in seen_by_pipeline.get(write.run_type, set())]
        assert not missing, f'always written, but not by these pipelines\' census runs: {missing}'

    def test_a_member_covered_elsewhere_is_not_seen_here(self, census_runs):
        """An entry the census produces after all no longer belongs in the table."""
        seen = set()
        for run_dir in census_runs:
            seen |= set(members_present(run_dir))
        assert not seen & set(_COVERED_ELSEWHERE), sorted(seen & set(_COVERED_ELSEWHERE))

    def test_each_covering_test_exists(self):
        for member_id, (test_file, _) in _COVERED_ELSEWHERE.items():
            assert (_PROJECT_ROOT / test_file).is_file(), f'{member_id}: {test_file}'


class TestTheStreamsAndTheIndexAgree:
    """What the files say and what the run list serves match the declaration."""

    def test_every_stream_names_its_declared_version(self, census_runs):
        for run_dir in census_runs:
            assert stream_version_mismatches(run_dir) == [], run_dir

    def test_the_index_row_lists_what_the_directory_holds(self, census_runs):
        index = RunIndex(AppConfigManager().get_file_logging_config_object().run_index)
        rows = {run.run_id: run for run in index.list_runs()}
        for run_dir in census_runs:
            row = rows[run_dir.name]
            files = run_files(run_dir)
            for column, served in ((RunIndexColumn.ARTIFACTS, row.artifacts),
                                   (RunIndexColumn.STREAM_FILES, row.stream_files)):
                expected = sorted(path.split('/', 1)[1] for path in files
                                  if any(member.matches(path)
                                         for member in RUN_INDEX_COLUMN_MEMBERS[column]))
                assert served == expected, (run_dir.name, column)


class TestTheCensusCanFail:
    """The checks find what they are for — on a copy of a real run directory."""

    @pytest.fixture
    def copy(self, census_sweep, tmp_path) -> Path:
        target = tmp_path / census_sweep.run_dirs[0].name
        shutil.copytree(census_sweep.run_dirs[0], target)
        return target

    def test_a_planted_file_is_named(self, copy):
        (copy / IO_SUBDIR / 'notes.json').write_text('{}', encoding='utf-8')
        assert undeclared_files(copy) == [f'{IO_SUBDIR}/notes.json']

    def test_a_file_of_the_other_pipeline_is_named(self, copy):
        session_only = RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.STATE_FRAMES]
        assert not session_only.is_written_by(RUN_TYPE_SIMULATION)
        (copy / session_only.example()).write_text('{}\n', encoding='utf-8')
        assert undeclared_files(copy) == [session_only.example()]

    def test_a_deleted_member_is_missed(self, copy):
        trade_history = RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.TRADE_HISTORY]
        assert RunEntryMemberId.TRADE_HISTORY in members_present(copy)
        (copy / trade_history.example()).unlink()
        assert RunEntryMemberId.TRADE_HISTORY not in members_present(copy)

    def test_a_stream_with_another_version_is_named(self, copy):
        stream = next(member for member in RUN_ENTRY_MEMBERS
                      if member.shape is EntryMemberShape.STREAM
                      and member.member_id in members_present(copy))
        path = copy / stream.example()
        lines = path.read_text(encoding='utf-8').splitlines()
        header = json.loads(lines[0])
        header['schema_version'] = 'not-declared'
        path.write_text('\n'.join([json.dumps(header)] + lines[1:]) + '\n', encoding='utf-8')
        assert len(stream_version_mismatches(copy)) == 1


class TestWithoutLogFilesTheRecordStays:
    """
    The log switch decides the log files alone: a session with it off still writes its header,
    its reports and its streams, and nothing undeclared appears in their place.
    """

    def test_a_session_without_log_files_is_still_a_declared_run(
            self, census_session_without_log_files):
        run_dir = census_session_without_log_files
        present = members_present(run_dir)
        assert undeclared_files(run_dir) == []
        for member_id in (RunEntryMemberId.HEADER, RunEntryMemberId.TRADE_HISTORY,
                          RunEntryMemberId.ORDER_EVENTS, RunEntryMemberId.STATE_FRAMES):
            assert member_id in present, member_id
        logs = [member_id for member_id in present
                if RUN_ENTRY_MEMBERS_BY_ID[member_id].shape is EntryMemberShape.LOG]
        assert logs == [], f'log files written with the switch off: {logs}'
