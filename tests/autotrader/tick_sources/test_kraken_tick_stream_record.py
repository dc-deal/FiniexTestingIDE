"""
FiniexTestingIDE - The live tick stream's own record (#609)

What the Kraken tick source does when its connection fails, and what it writes down about it:
the backoff starts over only after a connection has delivered, silence is measured on the
monotonic clock, every outage is recorded with the public trade ids around it, a trade without a
trustworthy venue time is refused and counted, and stop() wakes the loop wherever it waits.

The loop runs against a WebSocket server on the local machine, scripted per connection — the real
`websockets` library on both ends, no venue. Thresholds are shrunk to fractions of a second.
"""

import asyncio
import json
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, ConnectionClosedError
from websockets.frames import Close

from python.framework.autotrader.tick_sources.kraken_tick_message_parser import (
    KrakenTickMessageParser,
)
from python.framework.autotrader.tick_sources.kraken_tick_source import (
    KrakenTickSource,
    classify_connection_end,
    needs_tls,
)
from python.framework.autotrader.tick_sources.tick_stream_recorder import (
    REFUSALS_LISTED_PER_REASON,
    TickStreamRecorder,
)
from python.framework.constants.run_layout_constants import TICK_STREAM_EVENTS_FILE
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.types.autotrader_types.tick_stream_event_types import (
    RefusedPublicTradeReason,
    TickStreamLossCause,
)
from python.framework.types.config_types.autotrader_defaults_config_types import TickSourceConfig
from tests.shared.recording_logger import RecordingLogger

_FRAMES = (Path(__file__).resolve().parents[2] / 'fixtures' / 'tick_sources'
           / 'kraken_ws_frames.json')
_PAIR = 'ETH/USD'
_T0 = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

# Thresholds shrunk for the loop tests: a far side is dead after 0.3 s of silence, and a
# connection is stable once it has delivered for 0.3 s.
_FAST = dict(
    reconnect_initial_delay_s=0.05,
    reconnect_max_delay_s=0.4,
    backoff_reset_after_s=0.3,
    connection_check_interval_s=0.05,
    connection_dead_s=0.3,
)


# === Helpers ===

def _trade(trade_id: Optional[int], price: Any = 2500.0, qty: Any = 0.01,
           timestamp: Any = '2026-10-20T12:00:00.000000Z') -> Dict[str, Any]:
    """One Kraken v2 trade entry; a field passed as None is left out."""
    entry = {'symbol': _PAIR, 'side': 'buy', 'price': price, 'qty': qty, 'ord_type': 'market',
             'trade_id': trade_id, 'timestamp': timestamp}
    return {key: value for key, value in entry.items() if value is not None}


def _trade_frame(*entries: Any) -> str:
    """A trade message holding the given entries."""
    return json.dumps({'channel': 'trade', 'type': 'update', 'data': list(entries)})


def _record(directory: Path) -> List[Dict[str, Any]]:
    """Every line of a session's tick stream record."""
    path = directory / TICK_STREAM_EVENTS_FILE
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def _outages(directory: Path) -> List[Dict[str, Any]]:
    """The outage lines of a session's record."""
    return [line for line in _record(directory) if line['record_kind'] == 'outage']


def _refusals(directory: Path) -> List[Dict[str, Any]]:
    """The refused-trade lines of a session's record."""
    return [line for line in _record(directory) if line['record_kind'] == 'refused_public_trade']


def _received_ticks(ticks: queue.Queue) -> List[Any]:
    """The ticks a source handed over, without the end-of-stream sentinel it adds when it stops."""
    received = []
    while not ticks.empty():
        tick = ticks.get_nowait()
        if tick is not None:
            received.append(tick)
    return received


def _wait_until(condition: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    """Poll a condition until it holds or the time is up."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


class _Clock:
    """A wall clock and a monotonic clock a test moves by hand."""

    def __init__(self):
        self.monotonic_s = 1000.0
        self.wall = _T0

    def advance(self, seconds: float) -> None:
        self.monotonic_s += seconds
        self.wall += timedelta(seconds=seconds)

    def read_wall(self) -> datetime:
        return self.wall

    def read_monotonic(self) -> float:
        return self.monotonic_s


def _recorder(directory: Path, clock: _Clock,
              logger: Optional[RecordingLogger] = None) -> TickStreamRecorder:
    """A recorder writing into a test directory on a hand-moved clock."""
    return TickStreamRecorder(
        symbol='ETHUSD', record_dir=directory, run_id='run-1', session_logger=logger,
        wall_clock=clock.read_wall, monotonic_clock=clock.read_monotonic)


# === The record ===

class TestTheRecordHasAHeader:

    def test_the_header_names_the_stream_and_the_run(self, tmp_path):
        _recorder(tmp_path, _Clock()).close()

        assert _record(tmp_path) == [{
            'record_kind': 'header', 'stream': 'tick_stream_events', 'schema_version': 1,
            'run_id': 'run-1', 'symbol': 'ETHUSD'}]

    def test_a_recorder_without_a_directory_keeps_no_record(self, tmp_path):
        recorder = TickStreamRecorder(symbol='ETHUSD')
        recorder.on_trade(1, _T0)
        recorder.on_connection_lost(TickStreamLossCause.SILENCE, 4.0)
        recorder.close()

        assert list(tmp_path.iterdir()) == []


class TestAnOutageIsOneLine:
    """From the loss of a connection to the first trade after it — never one line per attempt."""

    def test_an_outage_ends_with_the_first_trade_after_it(self, tmp_path):
        clock = _Clock()
        recorder = _recorder(tmp_path, clock)
        recorder.on_attempt()                       # the first connect is no outage
        recorder.on_restored()
        recorder.on_trade(107927359, _T0)
        clock.advance(5.0)
        recorder.on_connection_lost(TickStreamLossCause.SILENCE, 5821.4)
        recorder.on_attempt()                       # failed
        clock.advance(2.0)
        recorder.on_attempt()                       # succeeds
        clock.advance(0.7)
        recorder.on_restored()
        recorder.on_trade(107927373, _T0 + timedelta(seconds=14.14))
        outages_ended = recorder.get_outages_ended()
        recorder.close()

        assert outages_ended == 1
        [outage] = _outages(tmp_path)
        assert outage['outage_no'] == 1
        assert outage['detected_by'] == 'silence'
        assert outage['delivered_for_s'] == 5821.4
        assert outage['attempts'] == 2
        assert outage['outage_s'] == pytest.approx(2.7)
        assert outage['last_public_trade_id'] == 107927359
        assert outage['first_public_trade_id'] == 107927373
        assert outage['data_gap_s'] == pytest.approx(14.14)
        assert outage['lost_at'] == (_T0 + timedelta(seconds=5)).isoformat()

    def test_connections_that_drop_before_a_trade_fold_into_one_outage(self, tmp_path):
        recorder = _recorder(tmp_path, _Clock())
        recorder.on_trade(100, _T0)
        recorder.on_connection_lost(TickStreamLossCause.CLOSED_BY_VENUE, 600.0)
        for cause in (TickStreamLossCause.CLOSED_BY_VENUE, TickStreamLossCause.CONNECTION_ERROR):
            recorder.on_attempt()
            recorder.on_restored()
            recorder.on_connection_lost(cause, 0.0)
        recorder.on_attempt()
        recorder.on_restored()
        recorder.on_trade(101, _T0 + timedelta(seconds=3))
        recorder.close()

        [outage] = _outages(tmp_path)
        assert outage['detected_by'] == 'closed_by_venue', 'the first loss is the one it reports'
        assert outage['delivered_for_s'] == 600.0
        assert outage['attempts'] == 3
        assert (outage['last_public_trade_id'], outage['first_public_trade_id']) == (100, 101)

    def test_a_loss_before_any_trade_names_no_last_id(self, tmp_path):
        recorder = _recorder(tmp_path, _Clock())
        recorder.on_connection_lost(TickStreamLossCause.CLOSED_BY_VENUE, 0.2)
        recorder.on_attempt()
        recorder.on_restored()
        recorder.on_trade(5, _T0)
        recorder.close()

        [outage] = _outages(tmp_path)
        assert 'last_public_trade_id' not in outage
        assert 'data_gap_s' not in outage
        assert outage['first_public_trade_id'] == 5

    def test_an_outage_open_at_stop_is_written_with_what_is_known(self, tmp_path):
        recorder = _recorder(tmp_path, _Clock())
        recorder.on_trade(100, _T0)
        recorder.on_connection_lost(TickStreamLossCause.SILENCE, 3.0)
        recorder.on_attempt()
        outages_ended = recorder.get_outages_ended()
        recorder.close()

        assert outages_ended == 0, 'an outage the stream never came back from has not ended'
        [outage] = _outages(tmp_path)
        assert outage['last_public_trade_id'] == 100
        assert 'restored_at' not in outage
        assert 'first_public_trade_id' not in outage

    def test_a_restoration_lost_again_leaves_nothing_behind(self, tmp_path):
        """A connection that came back and was lost before any trade names no restoration."""
        clock = _Clock()
        recorder = _recorder(tmp_path, clock)
        recorder.on_trade(100, _T0)
        recorder.on_connection_lost(TickStreamLossCause.CLOSED_BY_VENUE, 600.0)
        recorder.on_attempt()
        clock.advance(1.0)
        recorder.on_restored()
        recorder.on_connection_lost(TickStreamLossCause.CLOSED_BY_VENUE, 0.0)
        for _ in range(15):
            clock.advance(60.0)
            recorder.on_attempt()
        recorder.close()

        [outage] = _outages(tmp_path)
        assert outage['attempts'] == 16
        assert 'restored_at' not in outage and 'outage_s' not in outage, (
            'the handshake that delivered nothing is not the restoration')

    def test_a_connection_still_up_at_stop_is_the_restoration(self, tmp_path):
        clock = _Clock()
        recorder = _recorder(tmp_path, clock)
        recorder.on_trade(100, _T0)
        recorder.on_connection_lost(TickStreamLossCause.SILENCE, 3.0)
        recorder.on_attempt()
        clock.advance(3.0)
        recorder.on_restored()
        recorder.close()

        [outage] = _outages(tmp_path)
        assert outage['outage_s'] == pytest.approx(3.0)
        assert 'first_public_trade_id' not in outage, 'no trade came before the stop'

    def test_a_trade_without_an_id_leaves_the_gap_unnamed(self, tmp_path):
        recorder = _recorder(tmp_path, _Clock())
        recorder.on_trade(None, _T0)
        recorder.on_connection_lost(TickStreamLossCause.CONNECTION_ERROR, 30.0)
        recorder.on_attempt()
        recorder.on_restored()
        recorder.on_trade(7, _T0 + timedelta(seconds=2))
        recorder.close()

        [outage] = _outages(tmp_path)
        assert 'last_public_trade_id' not in outage
        assert outage['data_gap_s'] == pytest.approx(2.0), 'the venue times still bound the gap'


class TestARefusedTradeIsCounted:

    def test_each_refusal_is_a_line_and_each_kind_is_said_once(self, tmp_path):
        logger = RecordingLogger()
        recorder = _recorder(tmp_path, _Clock(), logger)
        recorder.on_refused_trade(
            RefusedPublicTradeReason.VENUE_TIME_WITHOUT_OFFSET, 7, '2026-10-20T12:00:00')
        recorder.on_refused_trade(
            RefusedPublicTradeReason.VENUE_TIME_WITHOUT_OFFSET, 8, '2026-10-20T12:00:01')
        recorder.on_refused_trade(RefusedPublicTradeReason.INVALID_PRICE, 9, 'NaN')
        recorder.close()

        assert [line['public_trade_id'] for line in _refusals(tmp_path)] == [7, 8, 9]
        assert 'error' not in logger.levels(), 'a refused trade does not grade the session'
        warnings = logger.text_at('warning').splitlines()
        assert len(warnings) == 3, 'one per kind, then the sum at the end'
        assert 'has no UTC offset' in warnings[0]
        assert '3 trade(s)' in warnings[2]

    def test_only_the_first_of_each_reason_are_listed_and_the_totals_close_the_record(
            self, tmp_path):
        recorder = _recorder(tmp_path, _Clock())
        over_the_cap = REFUSALS_LISTED_PER_REASON + 5
        for trade_id in range(over_the_cap):
            recorder.on_refused_trade(
                RefusedPublicTradeReason.VENUE_TIME_WITHOUT_OFFSET, trade_id, '2026-10-20T12:00:00')
        recorder.on_refused_trade(RefusedPublicTradeReason.INVALID_PRICE, 900, 'NaN')
        recorder.close()

        listed = [line['public_trade_id'] for line in _refusals(tmp_path)]
        assert listed == list(range(REFUSALS_LISTED_PER_REASON)) + [900], \
            'a reason past its cap is counted, not listed - another reason still is'
        assert _record(tmp_path)[-1] == {
            'record_kind': 'refused_public_trade_totals', 'symbol': 'ETHUSD',
            'listed_per_reason': REFUSALS_LISTED_PER_REASON,
            'totals': {'venue_time_without_offset': over_the_cap, 'invalid_price': 1}}
        assert recorder.get_refusal_count() == over_the_cap + 1

    def test_every_reason_says_what_was_wrong(self):
        for reason in RefusedPublicTradeReason:
            assert reason.describe(), f'{reason} has no description for the session log'


class TestARecordThatCannotBeWritten:

    def test_closing_twice_writes_once(self, tmp_path):
        recorder = _recorder(tmp_path, _Clock())
        recorder.on_connection_lost(TickStreamLossCause.SILENCE, 1.0)
        recorder.close()
        recorder.close()

        assert len(_outages(tmp_path)) == 1

    def test_a_close_that_fails_never_raises(self, tmp_path, monkeypatch):
        """A full disk fails the final flush too; the session's end must not fail over it."""
        logger = RecordingLogger()
        recorder = _recorder(tmp_path, _Clock(), logger)

        def _full_disk(writer: JsonlStreamWriter) -> None:
            raise OSError(28, 'No space left on device')

        monkeypatch.setattr(JsonlStreamWriter, 'close', _full_disk)
        recorder.close()

        assert logger.levels().count('error') == 1

    def test_it_is_said_once_and_never_raised(self, tmp_path):
        occupied = tmp_path / 'not_a_directory'
        occupied.write_text('a file where the io/ folder should be', encoding='utf-8')
        logger = RecordingLogger()

        recorder = _recorder(occupied, _Clock(), logger)
        recorder.on_trade(1, _T0)
        recorder.on_connection_lost(TickStreamLossCause.SILENCE, 1.0)
        recorder.on_refused_trade(RefusedPublicTradeReason.INVALID_PRICE, 2, '0')
        recorder.close()

        assert logger.levels().count('error') == 1
        assert 'could not be written' in logger.text_at('error')


# === The parser ===

@pytest.fixture
def parser_and_record(tmp_path) -> Tuple[KrakenTickMessageParser, TickStreamRecorder, Path]:
    """A parser reporting into a recorder that writes into a test directory."""
    recorder = TickStreamRecorder(symbol='ETHUSD', record_dir=tmp_path,
                                  session_logger=RecordingLogger())
    return KrakenTickMessageParser('ETHUSD', recorder=recorder), recorder, tmp_path


class TestTheParserRefusesInsteadOfRepairing:
    """A missing event time is never replaced by the local clock: the trade is refused, counted."""

    @pytest.mark.parametrize('entry, reason', [
        (_trade(11, timestamp=None), RefusedPublicTradeReason.NO_VENUE_TIME),
        (_trade(12, timestamp=''), RefusedPublicTradeReason.NO_VENUE_TIME),
        (_trade(13, timestamp=1760961600), RefusedPublicTradeReason.UNREADABLE_VENUE_TIME),
        (_trade(14, timestamp='yesterday'), RefusedPublicTradeReason.UNREADABLE_VENUE_TIME),
        (_trade(15, timestamp='2026-10-20T12:00:00'),
         RefusedPublicTradeReason.VENUE_TIME_WITHOUT_OFFSET),
        (_trade(16, price=float('nan')), RefusedPublicTradeReason.INVALID_PRICE),
        (_trade(17, price=float('inf')), RefusedPublicTradeReason.INVALID_PRICE),
        (_trade(18, price=0), RefusedPublicTradeReason.INVALID_PRICE),
        (_trade(19, qty=None), RefusedPublicTradeReason.INVALID_QUANTITY),
        (_trade(20, qty=-0.5), RefusedPublicTradeReason.INVALID_QUANTITY),
    ])
    def test_the_trade_is_refused_and_recorded(self, parser_and_record, entry, reason):
        parser, recorder, directory = parser_and_record

        ticks = parser.parse_message(_trade_frame(entry))
        recorder.close()

        assert ticks is None
        [refusal] = _refusals(directory)
        assert refusal['reason'] == reason.value
        assert refusal['public_trade_id'] == entry['trade_id']

    def test_an_entry_that_is_not_an_object_is_refused(self, parser_and_record):
        parser, recorder, directory = parser_and_record

        assert parser.parse_message(_trade_frame(1, None)) is None
        recorder.close()

        assert [line['reason'] for line in _refusals(directory)] == ['unreadable_entry'] * 2

    def test_data_that_is_not_a_list_is_refused(self, parser_and_record):
        parser, recorder, directory = parser_and_record

        frame = json.dumps({'channel': 'trade', 'type': 'update', 'data': 5})
        assert parser.parse_message(frame) is None
        recorder.close()

        assert [line['reason'] for line in _refusals(directory)] == ['unreadable_entry']

    def test_an_offset_other_than_utc_is_converted(self, parser_and_record):
        parser, _, _ = parser_and_record

        frame = _trade_frame(_trade(21, timestamp='2026-10-20T14:00:00+02:00'))
        [tick] = parser.parse_message(frame)

        assert tick.timestamp == _T0
        assert tick.timestamp.utcoffset() == timedelta(0)

    def test_a_trade_without_an_id_still_becomes_a_tick(self, parser_and_record):
        parser, _, _ = parser_and_record

        ticks = parser.parse_message(_trade_frame(_trade(None)))

        assert ticks is not None and len(ticks) == 1

    def test_the_public_id_reaches_the_record_and_never_the_tick(self, parser_and_record):
        parser, recorder, directory = parser_and_record
        recorder.on_connection_lost(TickStreamLossCause.SILENCE, 1.0)

        [tick] = parser.parse_message(_trade_frame(_trade(107927373)))
        recorder.close()

        assert _outages(directory)[0]['first_public_trade_id'] == 107927373
        assert not hasattr(tick, 'public_trade_id') and not hasattr(tick, 'trade_id'), (
            'the id stays on the transport plane: no worker can reach it')

    def test_every_recorded_venue_frame_passes(self, parser_and_record):
        parser, recorder, directory = parser_and_record
        frames = json.loads(_FRAMES.read_text(encoding='utf-8'))

        for name, frame in frames.items():
            if not name.startswith('_'):
                parser.parse_message(json.dumps(frame))
        recorder.close()

        assert _refusals(directory) == [], 'a real venue frame must never be refused'

    def test_a_ticker_entry_of_the_wrong_shape_is_ignored(self, parser_and_record):
        parser, _, _ = parser_and_record
        frame = json.dumps({'channel': 'ticker', 'type': 'update',
                            'data': [1, {'bid': float('nan'), 'ask': 2500.1}]})

        assert parser.parse_message(frame) is None
        assert parser.get_last_quote() is None


# === Why a connection ended ===

class TestTheLossCause:
    """Judged in a fixed order — our own silence check first, then what the close frames say."""

    def test_silence_wins_over_what_the_library_reports_after_the_abort(self):
        dropped = ConnectionClosedError(None, None)
        assert classify_connection_end(True, dropped, None, None) is TickStreamLossCause.SILENCE

    def test_an_exception_that_is_not_a_closed_connection_is_an_error(self):
        cause = classify_connection_end(False, RuntimeError('a defect'), None, None)
        assert cause is TickStreamLossCause.ERROR

    def test_an_unanswered_ping_is_a_ping_timeout(self):
        sent = Close(1011, 'keepalive ping timeout')
        cause = classify_connection_end(False, ConnectionClosedError(None, sent), None, sent)
        assert cause is TickStreamLossCause.PING_TIMEOUT

    def test_a_clean_close_by_the_venue_raises_nothing_and_is_still_named(self):
        cause = classify_connection_end(False, None, Close(1000, ''), Close(1000, ''))
        assert cause is TickStreamLossCause.CLOSED_BY_VENUE

    def test_a_close_with_an_error_code_from_the_venue_is_the_venue_closing(self):
        received = Close(1013, 'try again later')
        cause = classify_connection_end(
            False, ConnectionClosedError(received, None), received, None)
        assert cause is TickStreamLossCause.CLOSED_BY_VENUE

    def test_no_close_frame_at_all_is_a_connection_error(self):
        cause = classify_connection_end(False, ConnectionClosedError(None, None), None, None)
        assert cause is TickStreamLossCause.CONNECTION_ERROR


# === The address ===

class TestTheAddressMustBeEncrypted:

    @pytest.mark.parametrize('url, encrypted', [
        ('wss://ws.kraken.com/v2', True), ('WSS://ws.kraken.com/v2', True),
        ('ws://127.0.0.1:9000', False), ('WS://localhost:9000', False)])
    def test_tls_is_read_off_the_parsed_scheme(self, url, encrypted):
        """The configuration accepts WSS:// too — the source must then connect with TLS."""
        assert needs_tls(url) is encrypted

    @pytest.mark.parametrize('url', ['wss://ws.kraken.com/v2', 'ws://127.0.0.1:9000',
                                     'ws://localhost:9000/v2', 'ws://[::1]:9000'])
    def test_an_encrypted_or_local_address_is_accepted(self, url):
        assert TickSourceConfig(ws_url=url).ws_url == url

    @pytest.mark.parametrize('url', ['ws://ws.kraken.com/v2', 'http://ws.kraken.com/v2'])
    def test_anything_else_is_refused_at_boot(self, url):
        with pytest.raises(ValueError, match='must use wss://'):
            TickSourceConfig(ws_url=url)


# === The loop against a local venue ===

class _LocalVenue:
    """
    A Kraken-shaped WebSocket server on the local machine. Each connection runs the next script,
    the last one repeating; the server notes when every connection opened and closed.
    """

    def __init__(self, scripts: List[Callable]):
        self._scripts = scripts
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self._stop: Optional[asyncio.Event] = None
        self.port = 0
        self.opened: List[float] = []
        self.closed: Dict[int, float] = {}
        self.marks: Dict[str, float] = {}

    def __enter__(self) -> '_LocalVenue':
        self._thread.start()
        assert self._ready.wait(5.0), 'the local venue did not start'
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=5.0)

    def url(self) -> str:
        return f'ws://127.0.0.1:{self.port}'

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.close()

    async def _serve(self) -> None:
        self._stop = asyncio.Event()
        async with serve(self._handle, '127.0.0.1', 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop.wait()

    async def _handle(self, websocket: Any) -> None:
        number = len(self.opened)
        self.opened.append(time.monotonic())
        script = self._scripts[min(number, len(self._scripts) - 1)]
        try:
            await script(self, websocket)
        except ConnectionClosed:
            pass
        finally:
            await websocket.wait_closed()
            self.closed[number] = time.monotonic()


async def _acknowledge(websocket: Any) -> None:
    """Answer the source's trade subscription the way Kraken does."""
    request = json.loads(await websocket.recv())
    await websocket.send(json.dumps({'method': 'subscribe', 'success': True, 'result': {
        'channel': request['params']['channel'], 'symbol': request['params']['symbol'][0]}}))


async def _heartbeats(websocket: Any, seconds: float) -> None:
    """Send Kraken's heartbeat every 50 ms for a while."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await websocket.send(json.dumps({'channel': 'heartbeat'}))
        await asyncio.sleep(0.05)


def _close_at_once() -> Callable:
    async def script(venue: _LocalVenue, websocket: Any) -> None:
        await _acknowledge(websocket)
        await websocket.close(1000)
    return script


def _deliver_then_close(trade_id: int, seconds: float) -> Callable:
    async def script(venue: _LocalVenue, websocket: Any) -> None:
        await _acknowledge(websocket)
        await websocket.send(_trade_frame(_trade(trade_id)))
        await _heartbeats(websocket, seconds)
        await websocket.close(1000)
    return script


def _deliver_and_stay(trade_id: int) -> Callable:
    async def script(venue: _LocalVenue, websocket: Any) -> None:
        await _acknowledge(websocket)
        await websocket.send(_trade_frame(_trade(trade_id, timestamp='2026-10-20T12:00:09Z')))
        while True:
            await _heartbeats(websocket, 1.0)
    return script


def _go_dead(trade_id: int) -> Callable:
    """Deliver one trade, then stop reading altogether — a far side that answers nothing, not even
    a close, so only an abort ends the connection without waiting out the close timeout."""
    async def script(venue: _LocalVenue, websocket: Any) -> None:
        await _acknowledge(websocket)
        await websocket.send(_trade_frame(_trade(trade_id)))
        venue.marks.setdefault('dead_after', time.monotonic())
        websocket.transport.pause_reading()
        await asyncio.sleep(1.5)
        websocket.transport.abort()
    return script


def _trade_before_ack(trade_id: int) -> Callable:
    """A trade that arrives ahead of its subscription's acknowledgement."""
    async def script(venue: _LocalVenue, websocket: Any) -> None:
        request = json.loads(await websocket.recv())
        await websocket.send(_trade_frame(_trade(trade_id, timestamp='2026-10-20T12:00:05Z')))
        await websocket.send(json.dumps({'method': 'subscribe', 'success': True, 'result': {
            'channel': request['params']['channel'], 'symbol': request['params']['symbol'][0]}}))
        while True:
            await _heartbeats(websocket, 1.0)
    return script


def _trade_before_refusal(trade_id: int) -> Callable:
    """A trade that arrives, then the subscription it came on is refused."""
    async def script(venue: _LocalVenue, websocket: Any) -> None:
        await websocket.recv()
        await websocket.send(_trade_frame(_trade(trade_id)))
        await websocket.send(json.dumps({'method': 'subscribe', 'success': False,
                                         'error': 'EGeneral:Invalid arguments'}))
        await websocket.wait_closed()
    return script


def _refuse() -> Callable:
    async def script(venue: _LocalVenue, websocket: Any) -> None:
        await websocket.recv()
        venue.marks.setdefault('refused', time.monotonic())
        await websocket.send(json.dumps({'method': 'subscribe', 'success': False,
                                         'error': 'EGeneral:Invalid arguments'}))
        await websocket.wait_closed()
    return script


def _drop_tcp(trade_id: int) -> Callable:
    async def script(venue: _LocalVenue, websocket: Any) -> None:
        await _acknowledge(websocket)
        await websocket.send(_trade_frame(_trade(trade_id)))
        await asyncio.sleep(0.1)
        websocket.transport.abort()
    return script


def _start(venue: _LocalVenue, record_dir: Path,
           **overrides: float) -> Tuple[KrakenTickSource, threading.Thread, queue.Queue]:
    """A Kraken tick source on its own thread, pointed at the local venue."""
    ticks: queue.Queue = queue.Queue()
    settings = dict(_FAST)
    settings.update(overrides)
    source = KrakenTickSource(
        symbol='ETHUSD', ws_pair=_PAIR, tick_queue=ticks, ws_url=venue.url(),
        quote_channel_enabled=False, session_logger=RecordingLogger(), record_dir=record_dir,
        run_id='run-1', **settings)
    thread = threading.Thread(target=source.start, daemon=True)
    thread.start()
    return source, thread, ticks


def _stop(source: KrakenTickSource, thread: threading.Thread) -> float:
    """Stop the source and return how long its thread took to end."""
    started = time.monotonic()
    source.stop()
    thread.join(timeout=3.0)
    assert not thread.is_alive(), 'stop() did not end the loop'
    return time.monotonic() - started


class TestTheLoopAgainstALocalVenue:

    def test_a_far_side_that_accepts_and_closes_is_not_dialled_every_second(self, tmp_path):
        with _LocalVenue([_close_at_once()]) as venue:
            source, thread, _ = _start(venue, tmp_path)
            time.sleep(1.2)
            reconnects = source.get_reconnect_count()
            _stop(source, thread)
            connections = len(venue.opened)

        # Starting over after every handshake would wait 0.025-0.05 s each time — about thirty
        # connections in 1.2 s. Climbing the ladder, the waits grow 0.05, 0.1, 0.2, 0.4.
        assert 3 <= connections <= 12, f'{connections} connections in 1.2 s'
        assert reconnects == 0, 'no trade came back, so no outage has ended'
        [outage] = _outages(tmp_path)
        assert outage['detected_by'] == 'closed_by_venue'
        assert outage['attempts'] >= 2
        assert 'first_public_trade_id' not in outage

    def test_the_backoff_starts_over_only_after_a_connection_delivered(self, tmp_path):
        scripts = [_close_at_once(), _close_at_once(), _close_at_once(),
                   _deliver_then_close(100, 0.5), _deliver_and_stay(105)]
        with _LocalVenue(scripts) as venue:
            source, thread, ticks = _start(venue, tmp_path, reconnect_initial_delay_s=0.1,
                                           reconnect_max_delay_s=3.2)
            assert _wait_until(lambda: source.get_reconnect_count() == 2), (
                'the stream never resumed')
            _stop(source, thread)
            opened, closed = list(venue.opened), dict(venue.closed)

        escalated_wait = opened[3] - closed[2]
        wait_after_stable = opened[4] - closed[3]
        # The ladder's own jitter spreads each wait over 0.5-1.0 of its step, so the two windows
        # do not touch: 0.4 s x 0.5-1.0 after three quick losses, 0.1 s x 0.5-1.0 after a stable
        # one.
        assert escalated_wait >= 0.19, 'three quick losses climb the ladder'
        assert wait_after_stable < 0.18, 'a connection that delivered starts the ladder over'
        assert len(_received_ticks(ticks)) == 2
        first, second = _outages(tmp_path)
        assert first['attempts'] == 3 and first['first_public_trade_id'] == 100
        assert second['detected_by'] == 'closed_by_venue'
        assert second['delivered_for_s'] >= 0.3
        assert (second['last_public_trade_id'], second['first_public_trade_id']) == (100, 105)
        assert second['attempts'] == 1

    def test_a_silent_connection_is_aborted_and_recorded_as_silence(self, tmp_path):
        with _LocalVenue([_go_dead(200), _deliver_and_stay(201)]) as venue:
            source, thread, ticks = _start(venue, tmp_path)
            assert _wait_until(lambda: source.get_reconnect_count() == 1), (
                'the stream never resumed')
            _stop(source, thread)
            back_after = venue.opened[1] - venue.marks['dead_after']

        # 0.3 s of silence, a short backoff, a new connection — where a close handshake with a
        # far side that answers nothing would first wait out its 5 s timeout.
        assert back_after < 1.2, f'the next connection came {back_after:.2f} s after the silence'
        [outage] = _outages(tmp_path)
        assert outage['detected_by'] == 'silence'
        assert (outage['last_public_trade_id'], outage['first_public_trade_id']) == (200, 201)
        assert len(_received_ticks(ticks)) == 2

    def test_a_trade_ahead_of_its_ack_is_used_once_the_subscription_stands(self, tmp_path):
        with _LocalVenue([_deliver_then_close(499, 0.05), _trade_before_ack(500)]) as venue:
            source, thread, ticks = _start(venue, tmp_path)
            assert _wait_until(lambda: source.get_reconnect_count() == 1), (
                'the stream never resumed')
            _stop(source, thread)

        assert len(_received_ticks(ticks)) == 2
        [outage] = _outages(tmp_path)
        assert (outage['last_public_trade_id'], outage['first_public_trade_id']) == (499, 500)
        assert 'restored_at' in outage, 'the restoration is known before the held trade is used'

    def test_a_trade_ahead_of_a_refused_subscription_is_dropped(self, tmp_path):
        scripts = [_deliver_then_close(600, 0.05), _trade_before_refusal(601),
                   _deliver_and_stay(602)]
        with _LocalVenue(scripts) as venue:
            source, thread, ticks = _start(venue, tmp_path)
            assert _wait_until(lambda: source.get_reconnect_count() == 1), (
                'the stream never resumed')
            _stop(source, thread)

        assert len(_received_ticks(ticks)) == 2, 'trade 601 came on a subscription that failed'
        [outage] = _outages(tmp_path)
        assert (outage['last_public_trade_id'], outage['first_public_trade_id']) == (600, 602)

    def test_a_refused_subscription_closes_its_socket(self, tmp_path):
        with _LocalVenue([_refuse()]) as venue:
            source, thread, _ = _start(venue, tmp_path)
            assert _wait_until(lambda: 0 in venue.closed), 'the refused socket was never closed'
            _stop(source, thread)
            open_after_refusal = venue.closed[0] - venue.marks['refused']

        assert open_after_refusal < 0.5

    def test_a_dropped_connection_is_a_connection_error(self, tmp_path):
        with _LocalVenue([_drop_tcp(300), _deliver_and_stay(302)]) as venue:
            source, thread, _ = _start(venue, tmp_path)
            assert _wait_until(lambda: source.get_reconnect_count() == 1), (
                'the stream never resumed')
            _stop(source, thread)

        [outage] = _outages(tmp_path)
        assert outage['detected_by'] == 'connection_error'
        assert (outage['last_public_trade_id'], outage['first_public_trade_id']) == (300, 302)

    def test_stop_wakes_a_long_backoff_and_the_open_outage_is_written(self, tmp_path):
        with _LocalVenue([_close_at_once()]) as venue:
            source, thread, _ = _start(venue, tmp_path, reconnect_initial_delay_s=30.0,
                                       reconnect_max_delay_s=60.0)
            assert _wait_until(lambda: 0 in venue.closed), 'the first connection never ended'
            time.sleep(0.2)
            took = _stop(source, thread)

        assert took < 1.0, f'stop() waited {took:.1f} s for a backoff of 15-30 s'
        [outage] = _outages(tmp_path)
        assert outage['detected_by'] == 'closed_by_venue'
        assert 'first_public_trade_id' not in outage
