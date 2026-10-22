"""
How a running session stands (#611): the latest state frame of each unit, read from the two ends of
its stream, with its age and whether the session still records — `current` while a session reading
the venue writes on its cadence, `stopped` when its frames stop without an end, `ended` once its
last frame is the one written at shutdown, and then the run's end classification beside it. A
replay is judged only at its end. Each absence of a frame names its cause.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from python.api.api_app import create_app
from python.framework.reporting.io.artifact_specs import WARNINGS_ERRORS_ARTIFACT
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.reporting.store.report_store import IO_SUBDIR, ReportStore
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import (
    RunHeader,
    TicksFrom,
    WarningsErrorsOutcome,
    WarningsErrorsReport,
)
from python.framework.types.api.state_frame_types import (
    STATE_FRAMES_SCHEMA_VERSION,
    STATE_FRAMES_STREAM,
    ProcessMemoryRow,
    StateFrame,
    StateFrameCause,
)
from python.framework.types.log_layout_types import RUN_TYPE_AUTOTRADER, STATE_FRAMES_FILE
from python.framework.types.run_outcome_types import RunOutcome

_RUN = '20261101_000012_4c7e19a2'
_URL = f'/api/v1/reports/runs/{_RUN}/state-frames/latest'


def _index(root: Path) -> Path:
    return root / 'index.parquet'


def _plant_session(root: Path, ticks_from: Optional[TicksFrom] = TicksFrom.VENUE) -> Path:
    """
    Register a session the way a real one registers itself, and return its io/ directory.

    Args:
        root: The test's tmp tree
        ticks_from: Where its ticks come from — the venue, or the archive for a replay

    Returns:
        The run's io/ directory, created
    """
    run_dir = root / 'autotrader' / 'my_profile' / _RUN
    (run_dir / IO_SUBDIR).mkdir(parents=True)
    RunIndex(_index(root)).register_run(
        RunHeader(run_id=_RUN, start_time=datetime(2026, 11, 1, tzinfo=timezone.utc),
                  run_type=RUN_TYPE_AUTOTRADER, run_name='my_profile', ticks_from=ticks_from),
        run_dir)
    return run_dir / IO_SUBDIR


def _frame(written_for: StateFrameCause, written_at: datetime, **fields) -> dict:
    return StateFrame(
        frame_time=written_at, written_at=written_at, written_for=written_for,
        ticks_processed=fields.pop('ticks_processed', 10), symbol='ETHUSD',
        account_currency='USD', balance=248.10, **fields).model_dump(mode='json')


def _write_stream(io_dir: Path, frames: list, schema_version: int = STATE_FRAMES_SCHEMA_VERSION,
                  tail: str = '') -> None:
    appender = RunStreamAppender(
        io_dir / STATE_FRAMES_FILE, STATE_FRAMES_STREAM, schema_version, _RUN, None, stops='-',
        header_fields={'unit_name': 'my_unit', 'cadence_timeframe': 'M1', 'cadence_s': 60.0})
    for frame in frames:
        appender.write(frame)
    appender.close()
    if tail:
        with open(io_dir / STATE_FRAMES_FILE, 'a', encoding='utf-8') as handle:
            handle.write(tail)


def _ago(seconds: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


@pytest.fixture
def client(tmp_path: Path):
    with patch('python.api.endpoints.reports_router.ReportStore',
               lambda: ReportStore(_index(tmp_path))):
        yield TestClient(create_app())


class TestARunningSession:

    def test_writing_on_its_cadence_is_current(self, client, tmp_path):
        io_dir = _plant_session(tmp_path)
        first = _frame(StateFrameCause.CADENCE, _ago(3600), ticks_processed=1,
                       process_memory=ProcessMemoryRow(rss_bytes=400, private_bytes=360))
        _write_stream(io_dir, [first, _frame(StateFrameCause.CADENCE, _ago(30))])

        body = client.get(_URL).json()

        assert body['key'] == ['unit_name']
        unit = body['units'][0]
        assert (unit['unit_name'], unit['cadence_timeframe'], unit['cadence_s']) == (
            'my_unit', 'M1', 60.0)
        assert unit['recording'] == 'current'
        assert 25 < unit['age_s'] < 120
        assert unit['frame']['written_for'] == 'cadence'
        assert unit['first_frame'] == {'written_at': first['written_at'],
                                       'process_memory': {'rss_bytes': 400, 'private_bytes': 360}}
        assert unit['outcome'] is None and unit['truncated_tail'] is False

    def test_silent_for_three_cadences_without_an_end_is_stopped(self, client, tmp_path):
        _write_stream(_plant_session(tmp_path), [_frame(StateFrameCause.CADENCE, _ago(300))])
        assert client.get(_URL).json()['units'][0]['recording'] == 'stopped'

    def test_a_frame_from_this_clocks_future_has_no_age_and_no_verdict(self, client, tmp_path):
        _write_stream(_plant_session(tmp_path), [_frame(StateFrameCause.CADENCE, _ago(-3600))])
        unit = client.get(_URL).json()['units'][0]
        assert (unit['age_s'], unit['recording']) == (None, None)

    def test_a_line_cut_off_mid_write_is_left_out(self, client, tmp_path):
        frame = _frame(StateFrameCause.CADENCE, _ago(30), ticks_processed=7)
        _write_stream(_plant_session(tmp_path), [frame], tail='{"record_kind": "frame", "ti')
        unit = client.get(_URL).json()['units'][0]
        assert unit['truncated_tail'] is True
        assert unit['frame']['ticks_processed'] == 7


class TestAnEndedSession:

    def test_says_how_it_ended_once_its_report_was_written(self, client, tmp_path):
        io_dir = _plant_session(tmp_path)
        _write_stream(io_dir, [_frame(StateFrameCause.CADENCE, _ago(90)),
                               _frame(StateFrameCause.SHUTDOWN, _ago(60))])
        write_artifact(WarningsErrorsReport(run_id=_RUN, outcome=WarningsErrorsOutcome(
            run_outcome=RunOutcome.FAILED, shutdown_mode='emergency', emergency_reason='my cause')),
            io_dir, WARNINGS_ERRORS_ARTIFACT)

        unit = client.get(_URL).json()['units'][0]

        assert unit['recording'] == 'ended'
        assert unit['outcome']['run_outcome'] == 'failed'
        assert unit['outcome']['emergency_reason'] == 'my cause'

    def test_before_its_report_has_no_outcome_yet(self, client, tmp_path):
        _write_stream(_plant_session(tmp_path), [_frame(StateFrameCause.SHUTDOWN, _ago(5))])
        unit = client.get(_URL).json()['units'][0]
        assert (unit['recording'], unit['outcome']) == ('ended', None)


class TestAReplay:
    """A replay's frames come at replay speed, so their age says nothing about whether it runs."""

    def test_is_given_no_verdict_while_it_runs(self, client, tmp_path):
        io_dir = _plant_session(tmp_path, ticks_from=TicksFrom.ARCHIVE)
        _write_stream(io_dir, [_frame(StateFrameCause.CADENCE, _ago(3600))])
        unit = client.get(_URL).json()['units'][0]
        assert unit['recording'] is None
        assert unit['age_s'] > 3500, 'the age is served all the same'

    def test_has_ended_once_its_last_frame_is_written(self, client, tmp_path):
        io_dir = _plant_session(tmp_path, ticks_from=TicksFrom.ARCHIVE)
        _write_stream(io_dir, [_frame(StateFrameCause.SHUTDOWN, _ago(3600))])
        assert client.get(_URL).json()['units'][0]['recording'] == 'ended'


class TestNoFrameNamesItsCause:

    def test_an_unknown_run(self, client, tmp_path):
        _plant_session(tmp_path)
        response = client.get('/api/v1/reports/runs/nope/state-frames/latest')
        assert (response.status_code, response.json()['error']) == (404, 'run_not_found')

    def test_a_run_that_records_none(self, client, tmp_path):
        _plant_session(tmp_path)
        response = client.get(_URL)
        assert response.status_code == 404
        assert response.json()['error'] == 'state_frames_not_recorded'

    def test_a_stream_with_its_header_only(self, client, tmp_path):
        _write_stream(_plant_session(tmp_path), [])
        response = client.get(_URL)
        assert response.status_code == 404
        assert response.json()['error'] == 'state_frame_not_written_yet'

    def test_a_stream_of_another_schema_is_unreadable(self, client, tmp_path):
        _write_stream(_plant_session(tmp_path), [_frame(StateFrameCause.CADENCE, _ago(30))],
                      schema_version=STATE_FRAMES_SCHEMA_VERSION + 1)
        response = client.get(_URL)
        assert (response.status_code, response.json()['error']) == (409, 'artifact_unreadable')


class TestTheRunListNamesTheStream:

    def test_once_the_session_opened_it(self, client, tmp_path):
        io_dir = _plant_session(tmp_path)
        _write_stream(io_dir, [_frame(StateFrameCause.CADENCE, _ago(30))])
        RunIndex(_index(tmp_path)).record_streams(_RUN, io_dir.parent)

        run = client.get('/api/v1/reports/runs').json()['runs'][0]

        assert run['stream_files'] == [STATE_FRAMES_FILE]
        assert run['artifacts'] == [], 'a stream is not a report artifact'
