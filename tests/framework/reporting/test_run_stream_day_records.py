"""
A session's streams sealed per trading day (#476): at a boundary each stream's day moves into an
immutable day record and its file starts afresh, and a reader gets the stream back as one — whole,
within a time window, at its two ends, or as an index of its days — exactly once per line, also
while a seal happens underneath it and after a session died in the middle of one.
"""

import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pyarrow.parquet as pq
import pytest

import python.framework.reporting.io.run_stream_appender as appender_module
import python.framework.reporting.io.run_stream_union_reader as union_module
from python.framework.autotrader.tick_sources.tick_stream_recorder import TickStreamRecorder
from python.framework.constants.run_layout_constants import (
    DAY_LABEL_FORMAT,
    RUN_TYPE_AUTOTRADER,
    STREAM_TRADING_DAY_FIELD,
)
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.reporting.io.order_event_stream_io import order_events_of
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.reporting.io.run_stream_day_record import record_time_of, seal_stream_day
from python.framework.reporting.io.run_stream_sealer import RunStreamSealer
from python.framework.reporting.io.run_stream_union_reader import (
    read_stream_union,
    read_stream_union_index,
)
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.api.report_types import RunHeader
from python.framework.types.autotrader_types.tick_stream_event_types import TickStreamLossCause
from python.framework.types.run_stream_types import StreamDaySource, StreamTimeWindow
from python.framework.types.store_member_types import RunEntryMemberId
from tests.shared.entry_census_helpers import assert_run_dir_declared
from tests.shared.recording_logger import RecordingLogger

_RUN_ID = '20261102_000000_d4y5e4l5'
_DAY_1 = date(2026, 11, 2)
_DAY_2 = date(2026, 11, 3)
_DAY_3 = date(2026, 11, 4)
_T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _close_open_streams(monkeypatch):
    """
    Close every stream a test left open, as a session does at its end — most tests stop a session
    in the middle of a day.
    """
    opened: List[RunStreamAppender] = []
    real_init = RunStreamAppender.__init__

    def track(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        opened.append(self)
    monkeypatch.setattr(RunStreamAppender, '__init__', track)
    yield
    for appender in opened:
        appender.close()


def _session(run_dir: Path, logger=None):
    """
    A session's run writer with its stream sealer, and its trade stream open.

    Returns:
        (the sealer, the run writer, the trade stream)
    """
    sealer = RunStreamSealer(logger or RecordingLogger())
    writer = RunEntryWriter(run_dir, RUN_TYPE_AUTOTRADER, streams=sealer)
    trades = writer.open_stream(RunEntryMemberId.TRADES, _RUN_ID, logger, stops='-',
                                header_fields={'unit_name': 'my_session'})
    return sealer, writer, trades


def _trade(number: int, at: datetime) -> dict:
    """One trade line, timed by its exit."""
    return {'position_id': f'pos_{number}', 'exit_time': at.isoformat(), 'net_pnl': number * 0.5}


def _write_trades(stream: RunStreamAppender, numbers: range, start: datetime) -> List[dict]:
    """Write one trade per number, a minute apart; the records written."""
    written = [_trade(number, start + timedelta(minutes=index))
               for index, number in enumerate(numbers)]
    for record in written:
        assert stream.write(record)
    return written


def _read(run_dir: Path, window=None) -> List[dict]:
    """The trade stream as the run's reader returns it."""
    return RunDirReader(run_dir).read_stream(RunEntryMemberId.TRADES, window).records


def _day_path(run_dir: Path, day: date) -> Path:
    """Where a trading day of the trade stream is sealed."""
    return RunDirReader(run_dir).path_of(RunEntryMemberId.TRADES_DAY,
                                         day=day.strftime(DAY_LABEL_FORMAT))


def _plant_run(run_dir: Path) -> None:
    """A session's header, so the run directory can be held to the declaration."""
    write_run_header(RunHeader(run_id=_RUN_ID, start_time=_T0, run_type=RUN_TYPE_AUTOTRADER,
                               run_name='my_bot'), run_dir)


def _header(path: Path) -> dict:
    """A stream file's first line."""
    return json.loads(path.read_text(encoding='utf-8').splitlines()[0])


class TestASealMovesTheDay:

    def test_the_day_moves_into_its_record_and_the_file_starts_afresh(self, tmp_path):
        sealer, writer, trades = _session(tmp_path)
        written = _write_trades(trades, range(1, 4), _T0)

        sealer.seal(_DAY_1, _DAY_2)

        assert _day_path(tmp_path, _DAY_1).is_file()
        hot = writer.path_of(RunEntryMemberId.TRADES)
        assert hot.read_text(encoding='utf-8').count('\n') == 1, 'only the next day\'s header'
        assert _header(hot)[STREAM_TRADING_DAY_FIELD] == _DAY_2.isoformat()
        assert _read(tmp_path) == written

    def test_the_day_record_keeps_every_line_as_written(self, tmp_path):
        sealer, writer, trades = _session(tmp_path)
        _write_trades(trades, range(1, 4), _T0)
        lines = writer.path_of(RunEntryMemberId.TRADES).read_text(encoding='utf-8').splitlines()

        sealer.seal(_DAY_1, _DAY_2)

        table = pq.read_table(_day_path(tmp_path, _DAY_1))
        assert table.column('line').to_pylist() == lines[1:]
        assert table.column('line_no').to_pylist() == [2, 3, 4]
        assert table.column('record_time').to_pylist()[0] == _T0

    def test_the_footer_says_what_the_day_holds(self, tmp_path):
        sealer, _, trades = _session(tmp_path)
        _write_trades(trades, range(1, 4), _T0)

        sealer.seal(_DAY_1, _DAY_2)

        summary = union_module.day_record_summary(_day_path(tmp_path, _DAY_1))
        assert (summary.stream, summary.run_id, summary.trading_day) == ('trades', _RUN_ID, _DAY_1)
        assert summary.record_count == 3
        assert (summary.first_time, summary.last_time) == (_T0, _T0 + timedelta(minutes=2))
        assert summary.header['unit_name'] == 'my_session'
        assert not summary.cut_last_line

    def test_a_day_with_no_record_is_sealed_too(self, tmp_path):
        sealer, _, _ = _session(tmp_path)

        sealer.seal(_DAY_1, _DAY_2)

        assert union_module.day_record_summary(_day_path(tmp_path, _DAY_1)).record_count == 0

    def test_the_seal_is_said_in_one_line(self, tmp_path):
        logger = RecordingLogger()
        sealer, _, trades = _session(tmp_path, logger)
        _write_trades(trades, range(1, 3), _T0)

        sealer.seal(_DAY_1, _DAY_2)

        lines = [message for _, message in logger.lines if 'Day records sealed' in message]
        assert len(lines) == 1 and 'trades 2' in lines[0] and str(_DAY_1) in lines[0]

    def test_a_stream_opened_after_a_seal_names_the_running_day(self, tmp_path):
        sealer, writer, _ = _session(tmp_path)
        sealer.seal(_DAY_1, _DAY_2)

        frames = writer.open_stream(RunEntryMemberId.STATE_FRAMES, _RUN_ID, None, stops='-')

        assert _header(frames.get_path())[STREAM_TRADING_DAY_FIELD] == _DAY_2.isoformat()

    def test_a_stream_written_before_the_first_seal_names_no_day(self, tmp_path):
        _, writer, _ = _session(tmp_path)
        assert STREAM_TRADING_DAY_FIELD not in _header(writer.path_of(RunEntryMemberId.TRADES))

    def test_a_backtest_stream_names_no_day_and_is_never_sealed(self, tmp_path):
        stream = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER).open_stream(
            RunEntryMemberId.TRADES, _RUN_ID, None, stops='-')
        written = _write_trades(stream, range(1, 3), _T0)

        assert STREAM_TRADING_DAY_FIELD not in _header(stream.get_path())
        assert _read(tmp_path) == written


class TestASealThatGoesWrong:

    def test_a_stream_that_stopped_writing_is_not_sealed(self, tmp_path, monkeypatch):
        sealer, writer, trades = _session(tmp_path)
        _write_trades(trades, range(1, 2), _T0)

        def refuse(self, record):
            raise OSError(28, 'No space left on device')
        monkeypatch.setattr(JsonlStreamWriter, 'write', refuse)
        trades.write(_trade(2, _T0))
        monkeypatch.undo()

        sealer.seal(_DAY_1, _DAY_2)

        assert not _day_path(tmp_path, _DAY_1).exists()
        assert len(_read(tmp_path)) == 1

    def test_a_day_that_cannot_be_sealed_goes_with_the_next(self, tmp_path, monkeypatch):
        """
        Said once; the file keeps the day and takes the next one too, and the next seal files
        both under the first day — every line once.
        """
        logger = RecordingLogger()
        sealer, _, trades = _session(tmp_path, logger)
        first = _write_trades(trades, range(1, 3), _T0)
        real_seal = appender_module.seal_stream_day

        def fail_once(*args, **kwargs):
            monkeypatch.setattr(appender_module, 'seal_stream_day', real_seal)
            raise OSError(28, 'No space left on device')
        monkeypatch.setattr(appender_module, 'seal_stream_day', fail_once)

        sealer.seal(_DAY_1, _DAY_2)
        second = _write_trades(trades, range(3, 5), _T0 + timedelta(days=1))
        sealer.seal(_DAY_2, _DAY_3)

        errors = [message for level, message in logger.lines if level == 'error']
        assert len(errors) == 1 and 'could not be sealed' in errors[0]
        assert not _day_path(tmp_path, _DAY_2).exists()
        assert union_module.day_record_summary(
            _day_path(tmp_path, _DAY_1)).record_count == 4
        assert _read(tmp_path) == first + second

    def test_a_file_that_cannot_start_afresh_keeps_its_day(self, tmp_path, monkeypatch):
        """
        The day record is written, the fresh file is refused: the record goes again, the file
        keeps the day and takes the next one, and the money record goes on being written.
        """
        logger = RecordingLogger()
        sealer, writer, trades = _session(tmp_path, logger)
        first = _write_trades(trades, range(1, 3), _T0)
        real_write = appender_module.write_text_atomic

        def fail_once(*args, **kwargs):
            monkeypatch.setattr(appender_module, 'write_text_atomic', real_write)
            raise PermissionError(13, 'Permission denied')
        monkeypatch.setattr(appender_module, 'write_text_atomic', fail_once)

        sealer.seal(_DAY_1, _DAY_2)

        assert not _day_path(tmp_path, _DAY_1).exists()
        assert trades.is_writing()
        second = _write_trades(trades, range(3, 5), _T0 + timedelta(days=1))
        assert _read(tmp_path) == first + second
        sealer.seal(_DAY_2, _DAY_3)

        errors = [message for level, message in logger.lines if level == 'error']
        assert len(errors) == 1 and 'could not be sealed' in errors[0]
        assert union_module.day_record_summary(
            _day_path(tmp_path, _DAY_1)).record_count == 4
        assert _header(writer.path_of(RunEntryMemberId.TRADES))[STREAM_TRADING_DAY_FIELD] == \
            _DAY_3.isoformat()
        assert _read(tmp_path) == first + second

    def test_a_record_that_cannot_be_withdrawn_loses_no_line(self, tmp_path, monkeypatch):
        """
        Neither the fresh file nor the removal of the record goes through: the reader takes the
        day from its record and the lines after it with the next seal — each once.
        """
        sealer, _, trades = _session(tmp_path)
        first = _write_trades(trades, range(1, 3), _T0)
        real_write = appender_module.write_text_atomic

        def fail_once(*args, **kwargs):
            monkeypatch.setattr(appender_module, 'write_text_atomic', real_write)
            raise PermissionError(13, 'Permission denied')

        def refuse_unlink(self, missing_ok=False):
            raise PermissionError(13, 'Permission denied')
        monkeypatch.setattr(appender_module, 'write_text_atomic', fail_once)
        monkeypatch.setattr(Path, 'unlink', refuse_unlink)
        sealer.seal(_DAY_1, _DAY_2)
        monkeypatch.undo()

        second = _write_trades(trades, range(3, 5), _T0 + timedelta(days=1))
        assert _read(tmp_path) == first
        sealer.seal(_DAY_2, _DAY_3)

        assert _read(tmp_path) == first + second

    def test_a_session_killed_between_the_record_and_the_fresh_file_reads_each_line_once(
            self, tmp_path):
        _, writer, trades = _session(tmp_path)
        written = _write_trades(trades, range(1, 4), _T0)
        trades.close()
        hot = writer.path_of(RunEntryMemberId.TRADES)

        # The day record is written; the fresh file is not
        seal_stream_day(hot, _day_path(tmp_path, _DAY_1), ('exit_time',), _DAY_1)

        assert _read(tmp_path) == written
        ends = RunDirReader(tmp_path).read_stream_ends(RunEntryMemberId.TRADES)
        assert ends.last == written[-1]

    def test_the_same_on_a_later_day_whose_file_names_its_day(self, tmp_path):
        sealer, writer, trades = _session(tmp_path)
        day_one = _write_trades(trades, range(1, 3), _T0)
        sealer.seal(_DAY_1, _DAY_2)
        day_two = _write_trades(trades, range(3, 5), _T0 + timedelta(days=1))
        trades.close()

        seal_stream_day(writer.path_of(RunEntryMemberId.TRADES), _day_path(tmp_path, _DAY_2),
                        ('exit_time',), _DAY_2)

        assert _read(tmp_path) == day_one + day_two


class TestALineCutOffBeforeTheSeal:
    """
    A last line a failing write cut off is left out when its day is sealed, and every reader still
    says that a line was lost — the order-event report included.
    """

    def _sealed_with_a_cut_line(self, tmp_path: Path):
        """An order-event stream whose day ends in half a line, sealed; its reader."""
        sealer, writer, _ = _session(tmp_path)
        events = writer.open_stream(RunEntryMemberId.ORDER_EVENTS, _RUN_ID, None, stops='-',
                                    required=True)
        events.write({'scenario_name': 'my_session', 'seq': 1, 'event_type': 'submitted',
                      'order_id': 'pos_1', 'event_time': _T0.isoformat()})
        with writer.path_of(RunEntryMemberId.ORDER_EVENTS).open('a', encoding='utf-8') as handle:
            handle.write('{"scenario_name": "my_session", "seq": 2, "event_ty')
        sealer.seal(_DAY_1, _DAY_2)
        return RunDirReader(tmp_path)

    def test_the_footer_says_a_line_was_left_out(self, tmp_path):
        reader = self._sealed_with_a_cut_line(tmp_path)
        day = reader.day_record_paths(RunEntryMemberId.ORDER_EVENTS)[0]

        summary = union_module.day_record_summary(day)
        assert summary.cut_last_line and summary.record_count == 1

    def test_every_reader_still_says_so(self, tmp_path):
        reader = self._sealed_with_a_cut_line(tmp_path)

        stream = reader.read_stream(RunEntryMemberId.ORDER_EVENTS)
        assert stream.truncated_tail and len(stream.records) == 1
        assert reader.read_stream_ends(RunEntryMemberId.ORDER_EVENTS).truncated_tail
        rows, _, truncated = order_events_of(stream, 'order_events.jsonl', str(tmp_path))
        assert truncated and [row.seq for row in rows] == [1]


class TestWritingFromAnotherThread:

    def test_a_writer_on_another_thread_never_writes_into_a_seal(self, tmp_path):
        """
        The tick stream is written from the socket's thread while the loop seals: every line a
        write reported as written is read back once, in order.
        """
        sealer, _, trades = _session(tmp_path)
        written: List[dict] = []
        stop = threading.Event()

        def keep_writing():
            number = 0
            while not stop.is_set():
                number += 1
                record = _trade(number, _T0 + timedelta(seconds=number))
                if trades.write(record):
                    written.append(record)

        thread = threading.Thread(target=keep_writing)
        thread.start()
        day = _DAY_1
        for _ in range(20):
            sealer.seal(day, day + timedelta(days=1))
            day += timedelta(days=1)
        stop.set()
        thread.join()

        assert written
        assert _read(tmp_path) == written


class TestReadingTheStreamAsOne:

    @pytest.mark.parametrize('seal_after', [[], [1], [3], [1, 2], [2, 5], [5]])
    def test_the_union_is_the_stream_unsealed(self, tmp_path, seal_after):
        sealer, _, trades = _session(tmp_path)
        written: List[dict] = []
        day = _DAY_1
        for number in range(1, 6):
            written += _write_trades(trades, range(number, number + 1),
                                     _T0 + timedelta(hours=number))
            if number in seal_after:
                sealer.seal(day, day + timedelta(days=1))
                day += timedelta(days=1)

        assert _read(tmp_path) == written

    @pytest.mark.parametrize('days_before', [0, 1])
    def test_a_read_a_seal_overtakes_reads_each_line_once(self, tmp_path, days_before):
        """
        The reader holds the stream's file when the session seals the day, and lists the days
        after it: the sealed day is there, and the file it read is left out.
        """
        sealer, writer, trades = _session(tmp_path)
        written: List[dict] = []
        day = _DAY_1
        for _ in range(days_before):
            written += _write_trades(trades, range(1, 3), _T0)
            sealer.seal(day, day + timedelta(days=1))
            day += timedelta(days=1)
        written += _write_trades(trades, range(3, 6), _T0 + timedelta(days=days_before))
        reader = RunDirReader(tmp_path)

        def seal_then_list() -> List[Path]:
            sealer.seal(day, day + timedelta(days=1))
            return reader.day_record_paths(RunEntryMemberId.TRADES)

        stream = read_stream_union(writer.path_of(RunEntryMemberId.TRADES), seal_then_list,
                                   ('exit_time',))

        assert stream.records == written

    def test_an_index_a_seal_overtakes_counts_each_record_once(self, tmp_path):
        """The running file is read once: a seal between two reads of it would count a day twice."""
        sealer, writer, trades = _session(tmp_path)
        _write_trades(trades, range(1, 4), _T0)
        reader = RunDirReader(tmp_path)

        def seal_then_list() -> List[Path]:
            sealer.seal(_DAY_1, _DAY_2)
            return reader.day_record_paths(RunEntryMemberId.TRADES)

        rows = read_stream_union_index(writer.path_of(RunEntryMemberId.TRADES), seal_then_list,
                                       ('exit_time',))

        assert [(row.trading_day, row.source, row.record_count) for row in rows] == [
            (_DAY_1, StreamDaySource.SEALED, 3)]

    def test_an_index_reads_the_running_file_once(self, tmp_path, monkeypatch):
        """
        Read twice, a seal between the two reads would describe two files: the bytes of the day
        that ended and the ends of the fresh one, counting the day twice.
        """
        sealer, writer, trades = _session(tmp_path)
        _write_trades(trades, range(1, 4), _T0)
        real_ends = union_module.read_stream_ends

        def seal_then_read(path):
            sealer.seal(_DAY_1, _DAY_2)
            return real_ends(path)
        monkeypatch.setattr(union_module, 'read_stream_ends', seal_then_read)

        rows = RunDirReader(tmp_path).read_stream_index(RunEntryMemberId.TRADES)

        assert sum(row.record_count for row in rows) == 3

    def test_a_time_window_reads_the_records_inside_it(self, tmp_path):
        sealer, _, trades = _session(tmp_path)
        day_one = _write_trades(trades, range(1, 4), _T0)
        sealer.seal(_DAY_1, _DAY_2)
        day_two = _write_trades(trades, range(4, 7), _T0 + timedelta(days=1))

        window = StreamTimeWindow(since=_T0 + timedelta(minutes=1),
                                  until=_T0 + timedelta(days=1, minutes=1))

        assert _read(tmp_path, window) == day_one[1:] + day_two[:1]

    def test_a_day_outside_the_window_is_not_opened(self, tmp_path, monkeypatch):
        sealer, _, trades = _session(tmp_path)
        _write_trades(trades, range(1, 3), _T0)
        sealer.seal(_DAY_1, _DAY_2)
        day_two = _write_trades(trades, range(3, 5), _T0 + timedelta(days=1))
        opened = []
        real_read = union_module.read_day_record_lines
        monkeypatch.setattr(union_module, 'read_day_record_lines',
                            lambda path, window: opened.append(path) or real_read(path, window))

        records = _read(tmp_path, StreamTimeWindow(since=_T0 + timedelta(days=1)))

        assert records == day_two and opened == []

    def test_the_ends_right_after_a_seal_come_from_the_sealed_day(self, tmp_path):
        sealer, _, trades = _session(tmp_path)
        written = _write_trades(trades, range(1, 4), _T0)
        sealer.seal(_DAY_1, _DAY_2)

        ends = RunDirReader(tmp_path).read_stream_ends(RunEntryMemberId.TRADES)

        assert (ends.first, ends.last) == (written[0], written[-1])
        assert ends.header[STREAM_TRADING_DAY_FIELD] == _DAY_2.isoformat()

    def test_the_ends_span_the_days_and_the_file(self, tmp_path):
        sealer, _, trades = _session(tmp_path)
        day_one = _write_trades(trades, range(1, 3), _T0)
        sealer.seal(_DAY_1, _DAY_2)
        day_two = _write_trades(trades, range(3, 5), _T0 + timedelta(days=1))

        ends = RunDirReader(tmp_path).read_stream_ends(RunEntryMemberId.TRADES)

        assert (ends.first, ends.last) == (day_one[0], day_two[-1])

    def test_the_index_lists_each_day_without_reading_its_records(self, tmp_path, monkeypatch):
        sealer, _, trades = _session(tmp_path)
        _write_trades(trades, range(1, 4), _T0)
        sealer.seal(_DAY_1, _DAY_2)
        _write_trades(trades, range(4, 6), _T0 + timedelta(days=1))
        monkeypatch.setattr(union_module, 'read_day_record_lines',
                            lambda *args: pytest.fail('the index read a day\'s records'))

        rows = RunDirReader(tmp_path).read_stream_index(RunEntryMemberId.TRADES)

        assert [(row.trading_day, row.source, row.record_count) for row in rows] == [
            (_DAY_1, StreamDaySource.SEALED, 3), (_DAY_2, StreamDaySource.HOT, 2)]
        assert rows[0].first_time == _T0
        assert rows[1].last_time == _T0 + timedelta(days=1, minutes=1)

    def test_a_sealed_day_s_footer_is_read_once(self, tmp_path, monkeypatch):
        sealer, _, trades = _session(tmp_path)
        _write_trades(trades, range(1, 3), _T0)
        sealer.seal(_DAY_1, _DAY_2)
        reads = []
        real_summary = union_module.read_day_record_summary
        monkeypatch.setattr(union_module, 'read_day_record_summary',
                            lambda path: reads.append(path) or real_summary(path))

        for _ in range(3):
            RunDirReader(tmp_path).read_stream_index(RunEntryMemberId.TRADES)

        assert len(reads) == 1

    def test_a_run_without_the_stream_reads_none(self, tmp_path):
        reader = RunDirReader(tmp_path)
        assert reader.read_stream(RunEntryMemberId.TRADES) is None
        assert reader.read_stream_ends(RunEntryMemberId.TRADES) is None
        assert reader.read_stream_index(RunEntryMemberId.TRADES) == []


class TestWhatTheSealWasDueFor:

    def test_a_stream_opened_after_the_last_seal_was_not_due(self, tmp_path):
        """A diagnostics sink declared after the session's only seal has no day record to miss."""
        sealer, writer, _ = _session(tmp_path)
        sealer.seal(_DAY_1, _DAY_2)
        writer.open_stream(RunEntryMemberId.DIAGNOSTICS_ROWS, _RUN_ID, None, stops='-',
                           sink='late_sink')

        assert sealer.get_due_day_members() == frozenset({RunEntryMemberId.TRADES_DAY})

    def test_no_seal_no_day_record_due(self, tmp_path):
        sealer, _, _ = _session(tmp_path)
        assert sealer.get_due_day_members() == frozenset()


class TestARecordsTime:

    def test_the_first_time_field_that_reads_is_the_record_s_time(self):
        record = {'lost_at': 'not a time', 'received_at': _T0.isoformat()}
        assert record_time_of(record, ('lost_at', 'received_at')) == _T0

    def test_a_record_without_a_readable_time_has_none(self):
        assert record_time_of({'lost_at': 'not a time'}, ('lost_at', 'received_at')) is None


class TestEveryStreamOfTheSessionIsSealed:

    def test_a_tick_stream_record_is_sealed_into_its_day(self, tmp_path):
        """
        The tick stream record is written only by a session that reads its ticks from a venue,
        so the census's mock session never seals one: this run directory is held to the
        declaration instead.
        """
        _plant_run(tmp_path)
        sealer = RunStreamSealer(RecordingLogger())
        writer = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER, streams=sealer)
        recorder = TickStreamRecorder(symbol='ETHUSD', run_writer=writer, run_id=_RUN_ID)
        recorder.on_connection_lost(TickStreamLossCause.SILENCE, 4.0)
        recorder.on_attempt()
        recorder.on_restored()
        # The first trade after the connection came back closes the outage and writes it
        recorder.on_trade(1, _T0)

        sealer.seal(_DAY_1, _DAY_2)
        recorder.close()

        day = RunDirReader(tmp_path).path_of(RunEntryMemberId.TICK_STREAM_EVENTS_DAY,
                                             day=_DAY_1.strftime(DAY_LABEL_FORMAT))
        summary = union_module.day_record_summary(day)
        assert summary.record_count == 1 and summary.first_time is not None
        assert_run_dir_declared(tmp_path)

    def test_each_diagnostics_sink_is_sealed_on_its_own(self, tmp_path):
        _plant_run(tmp_path)
        sealer = RunStreamSealer(RecordingLogger())
        writer = RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER, streams=sealer)
        for sink in ('entries', 'exits'):
            stream = writer.open_stream(RunEntryMemberId.DIAGNOSTICS_ROWS, _RUN_ID, None,
                                        stops='-', sink=sink)
            stream.write({'note': sink})

        sealer.seal(_DAY_1, _DAY_2)

        reader = RunDirReader(tmp_path)
        assert reader.read_stream(RunEntryMemberId.DIAGNOSTICS_ROWS, sink='exits').records == [
            {'note': 'exits'}]
        assert len(reader.day_record_paths(RunEntryMemberId.DIAGNOSTICS_ROWS, sink='entries')) == 1
        assert_run_dir_declared(tmp_path)
