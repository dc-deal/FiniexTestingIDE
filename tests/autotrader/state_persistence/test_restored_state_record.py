"""
The algo state a session found at its start is kept in its run (#476).

The store is overwritten while the session runs, so once the session has saved its first state, no
read of the store can say what the session started from any more. The session writes what it
found into its own run, once — taken or refused, with its age — and the comparison of a run with
its backtest reads it there.
"""

from datetime import datetime, timezone

from python.framework.autotrader.run_record.session_records import record_restored_algo_state
from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.api.report_types import RunHeader
from python.framework.types.persistence_types import RestoreContext, RestoredAlgoState
from python.framework.types.store_member_types import RunEntryMemberId
from tests.shared.entry_census_helpers import assert_run_dir_declared
from tests.shared.recording_logger import RecordingLogger

_SAVED = datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)
_FOUND = datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc)


def _context() -> RestoreContext:
    return RestoreContext(saved_at_utc=_SAVED, now_utc=_FOUND, age_seconds=36_000.0,
                          trading_days=1, weekend_aware=False)


def test_the_found_state_lands_in_the_run_as_declared(tmp_path):
    """Covers the member for the run entry census, which no census session writes."""
    write_run_header(RunHeader(run_id='run_x', start_time=_FOUND, run_type=RUN_TYPE_AUTOTRADER,
                               run_name='my_bot'), tmp_path)

    record_restored_algo_state(tmp_path, 'run_x', {'phase': 'flat', 'streak': 2}, _context(),
                               True, RecordingLogger())

    path = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER).path_of(
        RunEntryMemberId.ALGO_STATE_RESTORED)
    record = RestoredAlgoState.model_validate_json(path.read_text())
    assert (record.accepted, record.trading_days, record.age_seconds) == (True, 1, 36_000.0)
    assert record.snapshot == {'phase': 'flat', 'streak': 2}
    assert record.saved_at_utc == _SAVED.isoformat()
    assert_run_dir_declared(tmp_path)


def test_a_refused_state_is_recorded_as_refused(tmp_path):
    record_restored_algo_state(tmp_path, 'run_x', {'phase': 'long'}, _context(), False,
                               RecordingLogger())

    path = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER).path_of(
        RunEntryMemberId.ALGO_STATE_RESTORED)
    assert RestoredAlgoState.model_validate_json(path.read_text()).accepted is False


def test_a_session_without_a_run_directory_writes_nothing():
    logger = RecordingLogger()
    record_restored_algo_state(None, 'run_x', {}, _context(), True, logger)
    assert logger.text_at('error') == ''
