"""
The run footprint (#621): what a run wrote as it says itself at its close, read against the
declaration and the disk. Every state a member can be in, and every state a run can be in.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from python.framework.constants.run_layout_constants import (
    FOOTPRINT_FILE,
    IO_SUBDIR,
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
)
from python.framework.reporting.console.run_footprint_summary import (
    CLOSE_LABELS,
    STATE_ORDER,
    render_run_footprint,
)
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS_BY_ID
from python.framework.store.entry_members.run_footprint import (
    assess,
    expectation_after,
    is_run_directory,
    read_footprint,
    write_footprint,
)
from python.framework.types.api.report_types import RunHeader, RunReporting
from python.framework.types.run_footprint_types import (
    MemberExpectation,
    MemberFootprintState,
    RunCloseState,
)
from python.framework.types.store_member_types import RunEntryMemberId

_RUN_ID = '20261023_100000_aaaaaaaa'
_START = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)


def _run(tmp_path: Path, run_type: str = RUN_TYPE_AUTOTRADER,
         reporting: RunReporting = RunReporting.EXPECTED, files=()) -> Path:
    """
    A run directory with a header and the given files.

    Args:
        tmp_path: Where the run directory goes
        run_type: Its pipeline
        reporting: Whether it was commissioned to report
        files: Paths inside the run to create

    Returns:
        The run directory
    """
    run_dir = tmp_path / _RUN_ID
    write_run_header(RunHeader(run_id=_RUN_ID, start_time=_START, run_type=run_type,
                               run_name='my_bot', reporting=reporting), run_dir)
    for relative in files:
        path = run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}', encoding='utf-8')
    return run_dir


def _state(assessment, member_id: RunEntryMemberId) -> MemberFootprintState:
    """The state the assessment gives one member."""
    return next(member.state for member in assessment.members if member.member_id is member_id)


_TRADE_HISTORY = RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.TRADE_HISTORY].example()


class TestTheFootprintIsTheClose:
    """A run with a footprint is closed; without one it is not, whatever else it holds."""

    def test_a_written_footprint_reads_back_with_every_file_but_itself(self, tmp_path):
        run_dir = _run(tmp_path, files=[_TRADE_HISTORY])
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [])

        record = read_footprint(run_dir)

        paths = {entry.path for entry in record.files}
        assert _TRADE_HISTORY in paths and 'header.json' in paths
        assert FOOTPRINT_FILE not in paths
        assert all(entry.size_bytes >= 0 for entry in record.files)

    def test_a_run_with_its_footprint_is_closed(self, tmp_path):
        run_dir = _run(tmp_path, files=[_TRADE_HISTORY])
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [])

        assessment = assess(run_dir)

        assert assessment.close_state is RunCloseState.CLOSED
        assert assessment.closed_at is not None
        assert _state(assessment, RunEntryMemberId.TRADE_HISTORY) is MemberFootprintState.PRESENT

    def test_a_run_without_one_is_not_closed_and_judges_nothing(self, tmp_path):
        assessment = assess(_run(tmp_path, files=[_TRADE_HISTORY]))

        assert assessment.close_state is RunCloseState.NOT_CLOSED
        assert assessment.has_problems()
        assert _state(assessment, RunEntryMemberId.COLD_START) is MemberFootprintState.UNKNOWN

    def test_a_run_still_going_is_running(self, tmp_path):
        assessment = assess(_run(tmp_path), running=True)

        assert assessment.close_state is RunCloseState.RUNNING
        assert not assessment.has_problems()

    def test_a_backtest_never_commissioned_to_report_is_not_held_to_a_close(self, tmp_path):
        run_dir = _run(tmp_path, run_type=RUN_TYPE_SIMULATION, reporting=RunReporting.NONE)

        assert assess(run_dir).close_state is RunCloseState.NOT_COMMISSIONED

    def test_an_unreadable_footprint_is_a_failed_close(self, tmp_path):
        run_dir = _run(tmp_path)
        (run_dir / FOOTPRINT_FILE).write_text('{"schema_version": 1', encoding='utf-8')

        assessment = assess(run_dir)

        assert assessment.close_state is RunCloseState.NOT_CLOSED
        assert _state(assessment, RunEntryMemberId.FOOTPRINT) is MemberFootprintState.FAILED


class TestWhatAClosedRunSaysAboutEachMember:
    """The states a member can be in once the run has closed."""

    def test_a_file_gone_since_the_close_is_missing(self, tmp_path):
        run_dir = _run(tmp_path, files=[_TRADE_HISTORY])
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [])
        (run_dir / _TRADE_HISTORY).unlink()

        assessment = assess(run_dir)

        assert _state(assessment, RunEntryMemberId.TRADE_HISTORY) is MemberFootprintState.MISSING
        assert assessment.has_problems()

    def test_a_file_written_after_the_close_is_late(self, tmp_path):
        run_dir = _run(tmp_path)
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [])
        (run_dir / IO_SUBDIR).mkdir(exist_ok=True)
        (run_dir / _TRADE_HISTORY).write_text('{}', encoding='utf-8')

        assert _state(assess(run_dir), RunEntryMemberId.TRADE_HISTORY) is MemberFootprintState.LATE

    def test_a_log_that_grows_after_the_close_is_not_late(self, tmp_path):
        run_dir = _run(tmp_path)
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [])
        day_log = RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.SESSION_DAY_LOG].example()
        (run_dir / day_log).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / day_log).write_text('a line', encoding='utf-8')

        assert (_state(assess(run_dir), RunEntryMemberId.SESSION_DAY_LOG)
                is MemberFootprintState.PRESENT)

    def test_a_member_always_written_and_absent_has_failed(self, tmp_path):
        run_dir = _run(tmp_path)
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [])

        assert (_state(assess(run_dir), RunEntryMemberId.TRADE_HISTORY)
                is MemberFootprintState.FAILED)

    @pytest.mark.parametrize('expected, state', [
        (False, MemberFootprintState.SKIPPED),
        (True, MemberFootprintState.FAILED),
    ])
    def test_a_member_written_only_sometimes_is_judged_by_the_run_s_expectation(
            self, tmp_path, expected, state):
        run_dir = _run(tmp_path)
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [MemberExpectation(
            member_id=RunEntryMemberId.COLD_START, expected=expected,
            reason='the boot found nothing to inherit')])

        assert _state(assess(run_dir), RunEntryMemberId.COLD_START) is state

    @pytest.mark.parametrize('held, reached, expected, reason', [
        (True, True, True, 'the profile records state frames'),
        (True, False, False, 'the session stopped before its tick loop'),
        (False, False, False, 'the profile records no state frames'),
    ])
    def test_a_member_of_a_later_step_reads_the_step_the_run_stopped_before(
            self, held, reached, expected, reason):
        found = expectation_after(RunEntryMemberId.STATE_FRAMES, held, reached,
                                  'the profile records state frames',
                                  'the profile records no state frames',
                                  'the session stopped before its tick loop')
        assert (found.expected, found.reason) == (expected, reason)

    def test_without_an_expectation_it_is_unknown_never_skipped(self, tmp_path):
        """A writer that failed must not read as a member that did not apply."""
        run_dir = _run(tmp_path)
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [])

        assert (_state(assess(run_dir), RunEntryMemberId.COLD_START)
                is MemberFootprintState.UNKNOWN)

    def test_a_file_nobody_declared_is_named(self, tmp_path):
        run_dir = _run(tmp_path, files=['notes.txt'])
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [])

        assessment = assess(run_dir)

        assert assessment.undeclared == ['notes.txt']
        assert assessment.has_problems()


class TestWhereARunDirectoryIs:

    def test_a_directory_with_a_header_is_a_run_and_one_without_is_not(self, tmp_path):
        assert is_run_directory(_run(tmp_path))
        assert not is_run_directory(tmp_path)


class TestTheView:
    """What `store_cli.py run-footprint` prints."""

    def test_every_state_has_its_place_and_every_close_its_words(self):
        assert set(STATE_ORDER) == set(MemberFootprintState)
        assert set(CLOSE_LABELS) == set(RunCloseState)

    def test_a_lost_file_is_printed_loud(self, tmp_path, capsys):
        run_dir = _run(tmp_path, files=[_TRADE_HISTORY])
        write_footprint(run_dir, _RUN_ID, RUN_TYPE_AUTOTRADER, [])
        (run_dir / _TRADE_HISTORY).unlink()

        render_run_footprint(assess(run_dir), _RUN_ID)

        printed = capsys.readouterr().out
        assert 'MISSING' in printed and _TRADE_HISTORY in printed
        assert 'closed' in printed

    def test_an_unknown_run_says_so(self, capsys):
        render_run_footprint(None, _RUN_ID)

        assert 'knows no directory' in capsys.readouterr().out
