"""
A unit's recorded streams over time (#580): its timeline — each stream with its record count and the
time of its first and latest record — and a window of its records a page at a time, read across the
trading days a session sealed and the file it still writes. Walking the window page by page from
where the last page ended meets every frame once; a refused parameter and every absence name their
cause.
"""

import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from python.api.api_app import create_app
from python.api.endpoints.reports_router import MAX_WINDOW_RECORDS
from python.framework.constants.run_layout_constants import (
    IO_SUBDIR,
    RUN_TYPE_AUTOTRADER,
    STATE_FRAMES_FILE,
)
from python.framework.reporting.io.run_stream_sealer import RunStreamSealer
from python.framework.reporting.store.report_store import ReportStore
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.api.report_types import RunHeader
from python.framework.types.api.state_frame_types import StateFrame, StateFrameCause
from python.framework.types.run_kind_types import TicksFrom
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.time_utils import floor_to_millisecond
from tests.shared.recording_logger import RecordingLogger

_RUN = '20261201_010500_ab12cd34'
_UNIT = 'my_session'
_BASE = f'/api/v1/reports/runs/{_RUN}/units/{_UNIT}/recorded-streams'
_DAY_1 = date(2026, 12, 1)
_DAY_2 = date(2026, 12, 2)
_DAY_3 = date(2026, 12, 3)


def _index(root: Path) -> Path:
    return root / 'index.parquet'


def _run_dir(root: Path) -> Path:
    """Register a session the way a real one registers itself, and return its directory."""
    run_dir = root / 'autotrader' / 'my_profile' / _RUN
    (run_dir / IO_SUBDIR).mkdir(parents=True)
    RunIndex(_index(root)).register_run(
        RunHeader(run_id=_RUN, start_time=datetime(2026, 12, 1, 1, 5, tzinfo=timezone.utc),
                  run_type=RUN_TYPE_AUTOTRADER, run_name='my_profile',
                  ticks_from=TicksFrom.VENUE),
        run_dir)
    return run_dir


def _frame(frame_time: datetime, written_at: datetime) -> dict:
    """One state frame, written on its cadence."""
    return StateFrame(
        frame_time=frame_time, written_at=written_at, written_for=StateFrameCause.CADENCE,
        ticks_processed=10, symbol='ETHUSD', account_currency='USD',
        balance=248.10).model_dump(mode='json')


def _session(root: Path):
    """
    A session's run writer with its stream sealer, and its state-frame stream open.

    Returns:
        (the sealer, the open stream)
    """
    sealer = RunStreamSealer(RecordingLogger())
    writer = RunEntryWriter(_run_dir(root), RUN_TYPE_AUTOTRADER, streams=sealer)
    stream = writer.open_stream(
        RunEntryMemberId.STATE_FRAMES, _RUN, None, stops='-',
        header_fields={'unit_name': _UNIT, 'cadence_timeframe': 'M1', 'cadence_s': 60.0})
    return sealer, stream


def _write_frames(stream, day: date, count: int, written_at: datetime,
                  microsecond: int = 412000, first_minute: int = 0) -> List[datetime]:
    """Write `count` frames a minute apart from 01:05 of a day; their frame times."""
    start = (datetime(day.year, day.month, day.day, 1, 5, 0, microsecond, tzinfo=timezone.utc)
             + timedelta(minutes=first_minute))
    times = [start + timedelta(minutes=minute) for minute in range(count)]
    for moment in times:
        assert stream.write(_frame(moment, written_at))
    return times


def _three_days(root: Path, written_at: datetime) -> List[datetime]:
    """
    A session over three trading days, two sealed and the third running: 3, 4 and 2 frames.

    Returns:
        Every frame time, oldest first
    """
    sealer, stream = _session(root)
    times = _write_frames(stream, _DAY_1, 3, written_at)
    sealer.seal(_DAY_1, _DAY_2)
    times += _write_frames(stream, _DAY_2, 4, written_at)
    sealer.seal(_DAY_2, _DAY_3)
    times += _write_frames(stream, _DAY_3, 2, written_at)
    stream.close()
    return times


def _declared_kinds() -> set:
    """The stream kinds the store declares, as the routes serve them."""
    return {member.recorded_stream_kind.value for member in RUN_ENTRY_MEMBERS
            if member.recorded_stream_kind is not None}


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace('+00:00', 'Z')


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def _follow(client: TestClient, held: Dict[int, datetime], counts: List[int]) -> None:
    """
    Follow a running unit one step, the way its document says: ask the timeline, then the window
    from the millisecond of the newest frame held, page by page, dropping the frames already held.

    Args:
        client: The API client
        held: The frames held so far, their time by their record_no — filled in
        counts: Each timeline answer's record count — appended to
    """
    timeline = client.get(_BASE)
    assert timeline.status_code == 200, timeline.json()
    counts.append(timeline.json()['streams'][0]['record_count'])
    params: Dict[str, object] = {'limit': 50}
    if held:
        params['from'] = _iso(floor_to_millisecond(max(held.values())))
    while True:
        answer = client.get(f'{_BASE}/window', params=params)
        assert answer.status_code == 200, answer.json()
        page = answer.json()
        for frame in page['state_frames']:
            moment = _parse(frame['frame_time'])
            # A frame already held keeps its number and its time in every answer
            assert held.setdefault(frame['record_no'], moment) == moment
        if not page['truncated']:
            return
        params['from'] = page['next_from']


@pytest.fixture
def client(tmp_path: Path):
    with patch('python.api.endpoints.reports_router.ReportStore',
               lambda: ReportStore(_index(tmp_path))):
        yield TestClient(create_app())


class TestTheTimeline:

    def test_a_running_unit_lists_its_stream_across_sealed_days_and_its_file(
            self, client, tmp_path):
        times = _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(_BASE).json()

        assert answer['unit_name'] == _UNIT and answer['run_id'] == _RUN
        assert answer['key'] == ['stream_kind', 'stream_name']
        (stream,) = answer['streams']
        assert (stream['stream_kind'], stream['stream_name'], stream['record_time_field']) == (
            'state_frames', 'state_frames', 'frame_time')
        assert stream['record_count'] == len(times) == 9
        assert _parse(stream['first_record_time']) == times[0]
        assert _parse(stream['last_record_time']) == times[-1]
        assert (stream['cadence_timeframe'], stream['cadence_s']) == ('M1', 60.0)
        assert answer['recording'] == 'current'

    def test_its_verdict_is_the_latest_frame_routes(self, client, tmp_path):
        _three_days(tmp_path, datetime.now(timezone.utc) - timedelta(hours=1))

        timeline = client.get(_BASE).json()
        latest = client.get(f'/api/v1/reports/runs/{_RUN}/state-frames/latest').json()

        assert timeline['recording'] == latest['units'][0]['recording'] == 'stopped'

    def test_its_verdict_is_judged_on_the_newest_frame(self, client, tmp_path):
        """Days written long ago do not make a session that wrote a frame just now look stopped."""
        now = datetime.now(timezone.utc)
        sealer, stream = _session(tmp_path)
        _write_frames(stream, _DAY_1, 3, now - timedelta(days=2))
        sealer.seal(_DAY_1, _DAY_2)
        _write_frames(stream, _DAY_2, 2, now - timedelta(days=1))
        _write_frames(stream, _DAY_2, 1, now)
        stream.close()

        timeline = client.get(_BASE).json()
        latest = client.get(f'/api/v1/reports/runs/{_RUN}/state-frames/latest').json()

        assert timeline['recording'] == latest['units'][0]['recording'] == 'current'

    def test_a_unit_before_its_first_seal_lists_its_running_file(self, client, tmp_path):
        now = datetime.now(timezone.utc)
        _, stream = _session(tmp_path)
        times = _write_frames(stream, _DAY_1, 3, now)
        stream.close()

        answer = client.get(_BASE).json()

        (row,) = answer['streams']
        assert row['record_count'] == 3
        assert (_parse(row['first_record_time']), _parse(row['last_record_time'])) == (
            times[0], times[-1])
        assert answer['recording'] == 'current'

    def test_it_lists_every_kind_the_store_declares(self, client, tmp_path):
        """A kind declared on a stream is claimed as served; a session recording it lists it."""
        _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(_BASE).json()
        window = client.get(f'{_BASE}/window', params={'limit': 1}).json()

        assert {row['stream_kind'] for row in answer['streams']} == _declared_kinds()
        assert set(window['keys']) == _declared_kinds()

    def test_a_stream_with_its_header_and_no_frame_counts_none(self, client, tmp_path):
        _, stream = _session(tmp_path)
        stream.close()

        (row,) = client.get(_BASE).json()['streams']

        assert row['record_count'] == 0
        assert row['first_record_time'] is None and row['last_record_time'] is None

    def test_a_stream_opened_before_its_header_names_why(self, client, tmp_path):
        (_run_dir(tmp_path) / IO_SUBDIR / STATE_FRAMES_FILE).touch()

        answer = client.get(_BASE)

        assert answer.status_code == 404
        assert answer.json()['error'] == 'state_frame_not_written_yet'


class TestEveryAbsenceNamesItsCause:

    @pytest.mark.parametrize('suffix', ['', '/window'])
    def test_an_unknown_run(self, client, suffix):
        answer = client.get(f'/api/v1/reports/runs/19990101_000000_00000000/units/{_UNIT}'
                            f'/recorded-streams{suffix}')

        assert answer.status_code == 404 and answer.json()['error'] == 'run_not_found'

    @pytest.mark.parametrize('suffix', ['', '/window'])
    def test_a_run_that_records_no_stream(self, client, tmp_path, suffix):
        _run_dir(tmp_path)

        answer = client.get(f'{_BASE}{suffix}')

        assert answer.status_code == 404 and answer.json()['error'] == 'streams_not_recorded'

    @pytest.mark.parametrize('suffix', ['', '/window'])
    def test_a_unit_the_run_does_not_record(self, client, tmp_path, suffix):
        _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(f'/api/v1/reports/runs/{_RUN}/units/other_unit/recorded-streams'
                            f'{suffix}')

        assert answer.status_code == 404 and answer.json()['error'] == 'unit_not_found'

    def test_a_stream_of_another_schema_is_unreadable(self, client, tmp_path):
        stream_file = _run_dir(tmp_path) / IO_SUBDIR / STATE_FRAMES_FILE
        stream_file.write_text('{"record_kind": "header", "stream": "state_frames", '
                               '"schema_version": 999, "run_id": "x", "unit_name": "my_session", '
                               '"cadence_timeframe": "M1", "cadence_s": 60.0}\n', encoding='utf-8')

        answer = client.get(_BASE)

        assert answer.status_code == 409 and answer.json()['error'] == 'artifact_unreadable'


class TestTheWindow:

    def test_left_open_it_serves_the_whole_stream_and_says_which_instants(
            self, client, tmp_path):
        times = _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(f'{_BASE}/window').json()

        assert [_parse(frame['frame_time']) for frame in answer['state_frames']] == times
        assert [frame['record_no'] for frame in answer['state_frames']] == list(range(1, 10))
        assert answer['keys'] == {'state_frames': ['record_no']}
        assert (answer['matched'], answer['returned'], answer['limit']) == (
            9, 9, MAX_WINDOW_RECORDS)
        assert answer['truncated'] is False and answer['next_from'] is None
        assert _parse(answer['window']['from']) == times[0].replace(microsecond=412000)
        assert _parse(answer['window']['to']) == times[-1].replace(microsecond=413000)

    @pytest.mark.parametrize('limit', [1, 2, 4, 9])
    def test_walking_the_pages_meets_every_frame_once(self, client, tmp_path, limit):
        times = _three_days(tmp_path, datetime.now(timezone.utc))
        seen: List[int] = []
        page = client.get(f'{_BASE}/window', params={'limit': limit}).json()
        seen += [frame['record_no'] for frame in page['state_frames']]
        while page['truncated']:
            page = client.get(f'{_BASE}/window',
                              params={'from': page['next_from'], 'limit': limit}).json()
            seen += [frame['record_no'] for frame in page['state_frames']]

        assert seen == list(range(1, len(times) + 1))

    def test_a_window_across_a_day_boundary_serves_both_sides(self, client, tmp_path):
        times = _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(f'{_BASE}/window', params={
            'from': _iso(times[1]), 'to': _iso(times[5])}).json()

        assert [_parse(frame['frame_time']) for frame in answer['state_frames']] == times[1:5]
        assert [frame['record_no'] for frame in answer['state_frames']] == [2, 3, 4, 5]

    def test_a_bound_left_out_is_the_millisecond_of_a_record_never_finer(self, client, tmp_path):
        """A client keeps time in milliseconds, so a bound it is handed back is one it can send."""
        _, stream = _session(tmp_path)
        times = _write_frames(stream, _DAY_1, 3, datetime.now(timezone.utc), microsecond=412789)
        stream.close()

        answer = client.get(f'{_BASE}/window').json()

        assert _parse(answer['window']['from']) == times[0].replace(microsecond=412000)
        assert _parse(answer['window']['to']) == times[-1].replace(microsecond=413000)
        assert [_parse(frame['frame_time']) for frame in answer['state_frames']] == times

    @pytest.mark.parametrize('params', [
        {'to': '2026-11-30T00:00:00Z'},
        {'from': '2026-12-04T00:00:00Z'},
    ], ids=['before-the-stream', 'after-the-stream'])
    def test_a_window_without_a_record_resolves_no_bound(self, client, tmp_path, params):
        """
        A bound left out is resolved from the records the window holds, so a window holding none
        leaves it null — never an end before its start, which the route itself would refuse.
        """
        _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(f'{_BASE}/window', params=params).json()

        assert answer['state_frames'] == [] and answer['matched'] == 0
        assert answer['truncated'] is False and answer['next_from'] is None
        for bound in ('from', 'to'):
            if bound in params:
                assert _parse(answer['window'][bound]) == _parse(params[bound])
            else:
                assert answer['window'][bound] is None, bound

    def test_a_stream_holding_its_header_alone_resolves_no_bound(self, client, tmp_path):
        _, stream = _session(tmp_path)
        stream.close()

        answer = client.get(f'{_BASE}/window').json()

        assert answer['window'] == {'from': None, 'to': None}
        assert (answer['matched'], answer['returned'], answer['state_frames']) == (0, 0, [])

    def test_seeking_serves_the_first_frame_at_or_after_the_instant(self, client, tmp_path):
        times = _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(f'{_BASE}/window', params={
            'from': _iso(times[4] - timedelta(seconds=20)), 'limit': 1}).json()

        (frame,) = answer['state_frames']
        assert _parse(frame['frame_time']) == times[4] and frame['record_no'] == 5
        assert answer['truncated'] is True
        assert _parse(answer['next_from']) == times[5].replace(microsecond=412000)

    @pytest.mark.parametrize('limit', [0, MAX_WINDOW_RECORDS + 1])
    def test_a_limit_outside_the_ceiling_is_refused_never_clamped(self, client, tmp_path, limit):
        _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(f'{_BASE}/window', params={'limit': limit})

        assert answer.status_code == 400 and answer.json()['error'] == 'invalid_limit'

    def test_a_window_that_ends_before_it_starts_is_refused(self, client, tmp_path):
        _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(f'{_BASE}/window', params={
            'from': '2026-12-02T00:00:00Z', 'to': '2026-12-01T00:00:00Z'})

        assert answer.status_code == 400 and answer.json()['error'] == 'invalid_range'

    def test_an_instant_that_is_not_iso_is_refused(self, client, tmp_path):
        _three_days(tmp_path, datetime.now(timezone.utc))

        answer = client.get(f'{_BASE}/window', params={'from': 'yesterday'})

        assert answer.status_code == 400 and answer.json()['error'] == 'invalid_timestamp'


class TestFollowingASessionWhileItWrites:
    """
    A running session is followed by asking (#380): the timeline every second or two, and the
    window from the newest frame held whenever the timeline moved. The session here writes and
    seals a trading day on a thread of its own while the routes are asked.
    """

    def test_following_meets_every_frame_once_across_a_seal(self, client, tmp_path):
        now = datetime.now(timezone.utc)
        sealer, stream = _session(tmp_path)
        written: List[datetime] = []
        failures: List[BaseException] = []
        # The session goes on only once the follower has seen the stage it is in, so the answers
        # come from before the seal and from after it, whatever the pace of the two threads
        followed_first_day = threading.Event()
        followed_after_seal = threading.Event()

        def run_session() -> None:
            try:
                written.extend(_write_frames(stream, _DAY_1, 300, now))
                followed_first_day.wait(timeout=30)
                sealer.seal(_DAY_1, _DAY_2)
                written.extend(_write_frames(stream, _DAY_2, 100, now))
                followed_after_seal.wait(timeout=30)
                written.extend(_write_frames(stream, _DAY_2, 200, now, first_minute=100))
            except BaseException as e:
                failures.append(e)

        session = threading.Thread(target=run_session)
        held: Dict[int, datetime] = {}
        counts: List[int] = []
        session.start()
        while session.is_alive():
            _follow(client, held, counts)
            if counts[-1]:
                followed_first_day.set()
            if counts[-1] > 300:
                followed_after_seal.set()
        session.join()
        stream.close()
        _follow(client, held, counts)

        assert not failures, failures
        assert counts == sorted(counts)
        assert any(0 < count <= 300 for count in counts)
        assert any(300 < count < len(written) for count in counts)
        assert sorted(held) == list(range(1, len(written) + 1))
        assert [held[number] for number in sorted(held)] == written
