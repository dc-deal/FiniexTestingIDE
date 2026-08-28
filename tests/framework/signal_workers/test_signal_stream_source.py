"""
FiniexTestingIDE - Signal Stream Transport (#468)

The push transport that replaces the interim pull path, against a local mock producer —
never against a real one. A suite that needs someone else's container running is a suite
that fails for reasons unrelated to the code.

What is worth pinning here is not that envelopes arrive. It is the behaviour around the
edges, where a transport quietly does the wrong thing for weeks: a cursor that advances
past a hole can never ask for the hole again, a rewind diagnosed as the wrong party's
resumes silently over a restored store, and a reconnect loop against a misspelled pipeline
id looks exactly like a healthy one on the panel.
"""

import inspect
import json
import time
from unittest.mock import MagicMock

import pytest

from python.framework.signal_data.transport.signal_inbox import SignalInbox
from python.framework.signal_data.transport import signal_stream_source
from python.framework.signal_data.transport.signal_frame_recorder import SignalFrameRecorder
from python.framework.signal_data.transport.signal_stream_source import (
    CONNECT_TIMEOUT_S,
    MINIMUM_WATCHDOG_S,
    SignalStreamSource,
)
from python.framework.types.config_types.sentiment_config_types import (
    ActiveProducer,
    ResolvedCredential,
    SentimentStreamConfig,
)
from python.framework.types.signal_data_types import (
    ProducerStreamSettings,
    SignalHealthStatus,
    SignalStreamCursor,
)
from tests.framework.signal_workers.conftest import MockStreamReply, MockStreamServer

SIGNAL_KIND = 'llm_sentiment'
PIPELINE = 'crypto_sentiment'
TOKEN = 'test-token'
BASE_MSC = 1787312488624


def envelope(seq: int, epoch: int = 1, **overrides) -> dict:
    """One envelope in the shape the stream carries."""
    payload = {
        'schema_version': '2.0',
        'pipeline_id': PIPELINE,
        'seq': seq,
        'stream_epoch': epoch,
        'available_msc': BASE_MSC + seq * 600_000,
        'trigger_reason': 'scheduled',
        'data_origin': 'live',
        'status': 'success',
        # Wire-shaped on purpose: `is_breaking` is a JSON boolean and `urgency` a number,
        # which is what the certificate's row checks assert and what a parsed model can no
        # longer report — Pydantic would turn a 1 into True before anyone could object.
        'result': [{'symbol': 'BTCUSD', 'signal': 'BUY', 'sentiment_score': 0.8,
                    'confidence': 0.9, 'basis': 'llm',
                    'is_breaking': False, 'urgency': 0.0}],
    }
    payload.update(overrides)
    return payload


def frame(event: str, payload: dict) -> bytes:
    """Render one SSE frame the way the producer writes it — one data line, no id."""
    return f'event: {event}\ndata: {json.dumps(payload)}\n\n'.encode('utf-8')


def signal_frame(seq: int, epoch: int = 1, **overrides) -> bytes:
    """One `signal` frame."""
    return frame('signal', envelope(seq, epoch, **overrides))


def control(code: str, **fields) -> bytes:
    """One `control` frame."""
    return frame('control', {'code': code, **fields})


def heartbeat(seq: int, epoch: int = 1) -> bytes:
    """One `heartbeat` frame."""
    return frame('heartbeat', {'stream_epoch': epoch, 'seq': seq,
                               'available_msc': BASE_MSC, 'now_msc': BASE_MSC + 41})


def wait_until(predicate, timeout_s: float = 4.0) -> bool:
    """
    Poll a predicate until it holds or the deadline passes.

    Args:
        predicate: Condition to wait for
        timeout_s: How long to wait

    Returns:
        True when the predicate held before the deadline
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def build(server: MockStreamServer, inbox: SignalInbox, cursor=None,
          token: str = TOKEN, pipeline_id: str = PIPELINE,
          heartbeat_s: float = 0.4, multiple: float = 2.0,
          recorder=None, health_probe=None) -> SignalStreamSource:
    """A stream source pointed at the mock producer, with test-scale timings."""
    return SignalStreamSource(
        config=SentimentStreamConfig(
            enabled=True, pipeline_id=pipeline_id,
            heartbeat_timeout_multiple=multiple,
            reconnect_backoff_initial_s=0.05, reconnect_backoff_max_s=0.1),
        producer=ActiveProducer(
            name='test', base_url=server.base_url(),
            credential=ResolvedCredential(token=token, source='tests (in-memory)')),
        stream_settings=ProducerStreamSettings(
            heartbeat_seconds=heartbeat_s, replay_window_hours=24.0),
        signal_kind=SIGNAL_KIND, inbox=inbox, logger=MagicMock(), cursor=cursor,
        frame_recorder=recorder, health_probe=health_probe)


@pytest.fixture
def inbox():
    """A fresh inbox per test."""
    return SignalInbox()


def running(server: MockStreamServer, inbox: SignalInbox, **kwargs):
    """
    Start a server and a source, and stop both afterwards.

    Args:
        server: The mock producer
        inbox: Inbox the transport fills

    Returns:
        A context manager yielding the started source
    """
    class _Running:
        def __enter__(self):
            server.start()
            self.source = build(server, inbox, **kwargs)
            self.source.start()
            return self.source

        def __exit__(self, *exc):
            self.source.stop()
            server.stop()

    return _Running()


class TestConnect:
    """The request itself — the most likely thing to be built wrong."""

    def test_a_first_session_asks_for_the_current_snapshot(self, inbox):
        """
        No cursor means the pre-stream archive, which carries none. `history` then, and
        never `since` — the producer refuses `since` without an epoch, by contract.
        """
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041) + control('live', stream_epoch=1, head_seq=1041))])
        with running(server, inbox):
            assert wait_until(lambda: inbox.get_total_received() >= 1)
        assert server.get_queries()[0] == f'/v1/stream/{PIPELINE}?history=1'

    def test_a_resumed_session_asks_from_its_cursor(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=control('live', stream_epoch=1, head_seq=1043))])
        cursor = SignalStreamCursor(epoch=1, seq=1043)
        with running(server, inbox, cursor=cursor):
            assert wait_until(lambda: server.get_connection_count() >= 1)
        assert server.get_queries()[0] == f'/v1/stream/{PIPELINE}?since=1043&epoch=1'

    def test_the_pipeline_travels_in_the_path_not_the_query(self, inbox):
        """
        Their authorization derives the grant from the route's first path parameter, so a
        query-parameter form would be authenticated but ungated. Ours must not drift back.
        """
        server = MockStreamServer([MockStreamReply(body=control('live', head_seq=0))])
        with running(server, inbox):
            assert wait_until(lambda: server.get_connection_count() >= 1)
        path = server.get_queries()[0].split('?')[0]
        assert path == f'/v1/stream/{PIPELINE}'

    def test_envelopes_reach_the_inbox_and_move_the_cursor(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041) + signal_frame(1042)
            + control('live', stream_epoch=1, head_seq=1042),
            hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 2)
            assert wait_until(lambda: source.get_transport_stats().state == 'live')
            assert source.get_cursor() == SignalStreamCursor(epoch=1, seq=1042)
            drained = inbox.drain()
            assert [s.seq for s in drained[SIGNAL_KIND]] == [1041, 1042]

    def test_a_keep_alive_is_recorded_without_touching_the_series(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=heartbeat(1041) + control('live', stream_epoch=1, head_seq=1041),
            hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(
                lambda: any('keep-alive' in event.message
                            for event in source.get_transport_stats().tape))
            assert inbox.get_total_received() == 0


class TestControlCodes:
    """Five codes, and the two rewind diagnoses must not collapse into one."""

    def test_live_on_an_empty_stream_is_not_an_error(self, inbox):
        """
        head_seq 0 is "nothing yet" and can never collide with a real seq — their counter
        returns seq+1, so the first envelope is 1.
        """
        server = MockStreamServer([MockStreamReply(
            body=control('live', stream_epoch=1, head_seq=0), hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: source.get_transport_stats().state == 'live')
            assert source.get_transport_stats().contract_errors == 0

    def test_replay_truncated_accepts_the_hole_instead_of_chasing_it(self, inbox):
        """
        The producer has said it does not hold those envelopes. Treating the resulting
        jump as a gap would reconnect for a replay they already refused.
        """
        server = MockStreamServer([MockStreamReply(
            body=control('replay_truncated', stream_epoch=1, requested_since=900,
                         oldest_available_seq=1038, window_hours=24)
            + signal_frame(1038) + control('live', stream_epoch=1, head_seq=1038),
            hold_s=0.5)])
        cursor = SignalStreamCursor(epoch=1, seq=900)
        with running(server, inbox, cursor=cursor) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            assert source.get_cursor() == SignalStreamCursor(epoch=1, seq=1038)
            assert source.get_stats()[2] == 0, 'no replay should have been requested'

    def test_epoch_changed_reconnects_at_the_new_epochs_head(self, inbox):
        """
        THEY rewound. Old sequence numbers mean nothing in the new epoch, so the cursor
        becomes its head — and the reconnect is the connect path, not a second resync.
        """
        server = MockStreamServer([
            MockStreamReply(body=control('epoch_changed', stream_epoch=2,
                                         previous_epoch=1, head_seq=7)),
            MockStreamReply(body=control('live', stream_epoch=2, head_seq=7), hold_s=0.5),
        ])
        cursor = SignalStreamCursor(epoch=1, seq=1043)
        with running(server, inbox, cursor=cursor) as source:
            assert wait_until(lambda: server.get_connection_count() >= 2)
            # ONE BEFORE the head. `since=N` serves N+1 onward, so resuming AT head_seq
            # would skip the newest envelope of the epoch we just moved to — once per
            # rewind, silently, and only ever visible as a hole nobody could explain.
            assert source.get_cursor() == SignalStreamCursor(epoch=2, seq=6)
        assert server.get_queries()[1] == f'/v1/stream/{PIPELINE}?since=6&epoch=2'

    def test_cursor_ahead_stops_and_never_resumes(self, inbox):
        """
        SOMEBODY ELSE rewound — most likely our own store was restored. Resuming silently
        would paper over exactly that, so the feed stops and a human decides.

        Terminal on both paths, confirmed from the producer's wire: they close after the
        frame. Their rule for the whole family — a control code that says the cursor is
        UNUSABLE is terminal; `replay_truncated` says it is merely too old, which is
        recoverable, so that one continues.
        """
        server = MockStreamServer([MockStreamReply(
            body=control('cursor_ahead', stream_epoch=1,
                         requested_since=9001, head_seq=1043))])
        cursor = SignalStreamCursor(epoch=1, seq=9001)
        with running(server, inbox, cursor=cursor) as source:
            assert wait_until(
                lambda: source.get_transport_stats().state == 'cursor_ahead')
            time.sleep(0.3)
            assert server.get_connection_count() == 1, 'a terminal frame must not reconnect'

    def test_a_terminal_frame_silences_the_identity_probe(self, inbox):
        """
        The probe borrows this transport's address to answer which journal the envelopes
        came from. Once there are no envelopes, asking every half hour for the rest of the
        session is a live component attached to a dead feed — and on the operator's panel
        it reads as something still working.
        """
        probe = MagicMock()
        probe.get_status.return_value = SignalHealthStatus()
        server = MockStreamServer([MockStreamReply(
            body=control('cursor_ahead', stream_epoch=1,
                         requested_since=9001, head_seq=1043))])
        cursor = SignalStreamCursor(epoch=1, seq=9001)
        with running(server, inbox, cursor=cursor, health_probe=probe) as source:
            assert wait_until(
                lambda: source.get_transport_stats().state == 'cursor_ahead')
            assert wait_until(lambda: probe.stop.called), (
                'a dead feed must stop asking the health route')

    def test_auth_revoked_stops_and_never_retries(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=control('auth_revoked', stream_epoch=1, detail='token expired'))])
        with running(server, inbox) as source:
            assert wait_until(
                lambda: source.get_transport_stats().state == 'unauthorized')
            time.sleep(0.3)
            assert server.get_connection_count() == 1

    def test_an_unknown_control_code_is_growth_and_not_a_fault(self, inbox):
        """
        Their vocabulary can grow in a MINOR. A reader that raised on it would turn an
        additive change into an outage.
        """
        server = MockStreamServer([MockStreamReply(
            body=control('sunspots', stream_epoch=1) + signal_frame(1041)
            + control('live', stream_epoch=1, head_seq=1041), hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            assert source.get_transport_stats().contract_errors == 0
            assert any('unknown control' in event.message
                       for event in source.get_transport_stats().tape)

    def test_an_unknown_event_name_is_growth_and_not_a_fault(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=frame('weather', {'sunny': True}) + signal_frame(1041)
            + control('live', stream_epoch=1, head_seq=1041), hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            assert source.get_transport_stats().contract_errors == 0


class TestUnreadableNonEnvelopeFrames:
    """
    A control or heartbeat frame we cannot parse is OUR schema disagreeing with their
    answer — never their outage.

    The signal path always handled this; control and heartbeat did not, and the asymmetry
    was the bug: an unguarded validation error escaped the frame handler into the reconnect
    loop's blanket catch, was counted as a TRANSPORT fault, and tore the connection down.
    A producer renaming one control field would have presented as an endless outage on
    their side.
    """

    def test_a_control_frame_with_no_code_is_a_contract_error(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=frame('control', {'stream_epoch': 1, 'head_seq': 3})
            + signal_frame(1041) + control('live', stream_epoch=1, head_seq=1041),
            hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            stats = source.get_transport_stats()
            assert stats.contract_errors == 1
            assert stats.transport_errors == 0, 'never their infrastructure'
            assert server.get_connection_count() == 1, 'the connection stays open'

    def test_a_heartbeat_with_a_mistyped_field_is_a_contract_error(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=frame('heartbeat', {'stream_epoch': 1, 'seq': 'soon'})
            + signal_frame(1041) + control('live', stream_epoch=1, head_seq=1041),
            hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            stats = source.get_transport_stats()
            assert stats.contract_errors == 1
            assert stats.transport_errors == 0
            assert server.get_connection_count() == 1


class TestRefusedRequests:
    """A refusal is not an outage, and retrying one forever hides the real cause."""

    def test_a_rejected_credential_stops_the_stream(self, inbox):
        server = MockStreamServer([MockStreamReply(body=b'')])
        with running(server, inbox, token='wrong') as source:
            assert wait_until(
                lambda: source.get_transport_stats().state == 'unauthorized')
            time.sleep(0.3)
            assert server.get_connection_count() == 1
            assert source.get_transport_stats().transport_errors == 0, (
                'a credential condition is never a transport fault')

    def test_an_unknown_pipeline_id_stops_instead_of_looping(self, inbox):
        """
        404 is "does not exist", which a reconnect cannot fix. A client that cannot tell it
        from "exists but idle" waits forever on a typo while the panel looks healthy.
        """
        server = MockStreamServer([MockStreamReply(body=b'')])
        with running(server, inbox, pipeline_id='mistyped_sentiment') as source:
            assert wait_until(
                lambda: source.get_transport_stats().state == 'misconfigured')
            time.sleep(0.3)
            assert server.get_connection_count() == 1

    def test_a_refused_parameter_combination_stops_too(self, inbox):
        server = MockStreamServer([MockStreamReply(status=400)])
        with running(server, inbox) as source:
            assert wait_until(
                lambda: source.get_transport_stats().state == 'misconfigured')
            time.sleep(0.3)
            assert server.get_connection_count() == 1


class TestGapRecovery:
    """A hole must be askable-for once, and acceptable afterwards."""

    def test_a_gap_reconnects_from_the_last_contiguous_position(self, inbox):
        """
        The envelope past the hole is still enqueued — withholding a valid envelope helps
        nobody — but the cursor stays behind the hole so the replay can fill it.
        """
        server = MockStreamServer([
            MockStreamReply(body=signal_frame(1041)
                            + control('live', stream_epoch=1, head_seq=1041)
                            + signal_frame(1044)),
            MockStreamReply(body=signal_frame(1042) + signal_frame(1043)
                            + control('live', stream_epoch=1, head_seq=1044),
                            hold_s=0.5),
        ])
        with running(server, inbox) as source:
            assert wait_until(lambda: server.get_connection_count() >= 2)
            assert wait_until(lambda: source.get_cursor()
                              == SignalStreamCursor(epoch=1, seq=1043))
            assert source.get_stats()[2] == 1, 'exactly one replay for one hole'
        assert server.get_queries()[1] == f'/v1/stream/{PIPELINE}?since=1041&epoch=1'

    def test_an_unfillable_hole_is_accepted_rather_than_chased_forever(self, inbox):
        """
        The second encounter of the same boundary means they cannot fill it. Reconnecting
        against it forever would turn a reported gap into an outage of our own making.
        """
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041) + control('live', stream_epoch=1, head_seq=1041)
            + signal_frame(1044), hold_s=0.4)])
        with running(server, inbox) as source:
            assert wait_until(lambda: source.get_cursor()
                              == SignalStreamCursor(epoch=1, seq=1044), timeout_s=5.0)
            assert source.get_stats()[2] == 1, 'the hole is asked for once, then accepted'

    def test_a_replayed_envelope_is_not_counted_twice(self, inbox):
        """
        A gap replay redelivers what was already accepted past the hole. Harmless for the
        series, which deduplicates by the same key — but a second count in the observed
        accumulator is a wrong number in the run report.
        """
        server = MockStreamServer([
            MockStreamReply(body=signal_frame(1041)
                            + control('live', stream_epoch=1, head_seq=1041)
                            + signal_frame(1044)),
            MockStreamReply(body=signal_frame(1042) + signal_frame(1043)
                            + signal_frame(1044)
                            + control('live', stream_epoch=1, head_seq=1044),
                            hold_s=0.5),
        ])
        with running(server, inbox) as source:
            assert wait_until(lambda: source.get_cursor()
                              == SignalStreamCursor(epoch=1, seq=1044), timeout_s=5.0)
            received = [s.seq for s in inbox.drain()[SIGNAL_KIND]]
        assert sorted(received) == [1041, 1042, 1043, 1044]


class TestTheCursorAcrossEpochs:
    """A seq is only comparable inside its own epoch."""

    def test_a_newer_epoch_takes_over_without_a_gap(self, inbox):
        """
        There is nothing to be contiguous with across a reset, so the new generation's
        first envelope moves the cursor rather than reading as a hole.
        """
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041, epoch=1) + signal_frame(4, epoch=2)
            + control('live', stream_epoch=2, head_seq=4), hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 2)
            assert source.get_cursor() == SignalStreamCursor(epoch=2, seq=4)
            assert source.get_stats()[2] == 0, 'a new epoch is not a gap'

    def test_an_older_epoch_cannot_move_a_newer_cursor(self, inbox):
        """
        A straggler from a superseded generation. Comparing its seq against the newer
        cursor would be comparing two different countings — and the first version of this
        compared it against None.
        """
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(9, epoch=2) + signal_frame(1041, epoch=1)
            + control('live', stream_epoch=2, head_seq=9), hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 2)
            assert source.get_cursor() == SignalStreamCursor(epoch=2, seq=9)
            assert source.get_transport_stats().transport_errors == 0


class TestTheUnknownEpoch:
    """
    `stream_epoch: 0` is the producer's "no counter row yet" — never generation zero.

    Their clarification, sent after they shipped the mirror image of it and caught it in
    test: reading 0 → N as a series change closed every consumer attached to a newly added
    pipeline with a false rewind. Our half is the other direction — never ADOPT 0 as a
    position, because `?epoch=0` on the next reconnect describes no series at all.
    """

    def test_an_unknown_epoch_is_never_taken_as_a_cursor(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(3, epoch=0) + control('live', stream_epoch=0, head_seq=3),
            hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            assert source.get_cursor() is None, (
                'epoch 0 is "not known yet" — adopting it would ask ?epoch=0 on reconnect')

    def test_the_first_real_epoch_is_adopted_after_an_unknown_one(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(3, epoch=0) + signal_frame(4, epoch=1)
            + control('live', stream_epoch=1, head_seq=4), hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 2)
            assert source.get_cursor() == SignalStreamCursor(epoch=1, seq=4)

    @pytest.mark.parametrize('epochs', [
        {'stream_epoch': 0, 'previous_epoch': 1},
        {'stream_epoch': 1, 'previous_epoch': 0},
    ])
    def test_a_rewind_announced_from_an_unknown_epoch_is_not_a_rewind(self, inbox, epochs):
        """
        Half a comparison is not a comparison. Alerting here would report a series change
        to the operator that never happened — the exact bug the producer caught on their
        own side, seen from ours.
        """
        server = MockStreamServer([
            MockStreamReply(body=control('epoch_changed', head_seq=4, **epochs)),
            MockStreamReply(body=control('live', stream_epoch=1, head_seq=4), hold_s=0.5),
        ])
        with running(server, inbox) as source:
            assert wait_until(lambda: server.get_connection_count() >= 2)
            assert source.get_cursor() is None
            assert not any('epoch changed' in event.message
                           for event in source.get_transport_stats().tape), (
                'a cold sequencer must not be announced as a rewind')


class TestTheOperatorState:
    """The panel must describe the transport NOW, not at the last thing that went wrong."""

    def test_a_resumed_connection_reads_as_replaying_until_the_live_frame(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1044), hold_s=1.0)])
        cursor = SignalStreamCursor(epoch=1, seq=1043)
        with running(server, inbox, cursor=cursor) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            assert source.get_transport_stats().state == 'replay'

    def test_a_good_envelope_clears_a_previous_contract_error(self, inbox):
        """
        The producer's beat is long, so a fault that only cleared on reconnect would sit on
        the panel for minutes — a healthy feed reading as a broken one, which is the exact
        misreading the panel exists to prevent.
        """
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041, available_msc='not-a-timestamp')
            + signal_frame(1042) + control('live', stream_epoch=1, head_seq=1042),
            hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            stats = source.get_transport_stats()
            assert stats.state == 'live'
            assert stats.contract_errors == 1, 'the count survives — only the state clears'


class TestASilentStretchInsideAConnection:
    """
    The shape a real producer actually has, and the one this suite could not produce.

    A stream is silent between keep-alives — 20 s at the producer's setting. Every other
    reply here writes its whole body at once and then either holds or closes, so the read
    loop never had to survive a quiet stretch and come back. It did not: the first version
    polled the socket with a short timeout, and CPython marks a socket file object
    PERMANENTLY timed out after its first expiry, so the second read raised a plain OSError
    rather than TimeoutError and the healthy connection was torn down as a transport fault.

    The suite's own timings hid it. With `heartbeat_seconds` 0.4 and multiple 2.0 the
    watchdog is 0.8 s, BELOW the 1 s poll — the one configuration in which a second poll
    never happens. Production is the opposite: 20 s served, multiple 3, so the poll always
    fired first and the transport would have degraded into a reconnect loop, which is worse
    than the pull path it replaces.
    """

    def test_a_quiet_stretch_does_not_end_the_connection(self, inbox):
        """
        Silence shorter than the watchdog is NORMAL, not a fault. One connection, both
        envelopes, nothing counted against the producer.
        """
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041) + control('live', stream_epoch=1, head_seq=1041),
            gap_s=2.5,
            tail=signal_frame(1042),
            hold_s=0.5)])
        with running(server, inbox, heartbeat_s=2.0, multiple=3.0) as source:
            assert wait_until(lambda: inbox.get_total_received() == 2, timeout_s=8.0)
            stats = source.get_transport_stats()
            assert server.get_connection_count() == 1, (
                'the connection must survive a quiet stretch, not be rebuilt through it')
            assert stats.transport_errors == 0
            assert stats.state == 'live'
            assert source.get_cursor() == SignalStreamCursor(epoch=1, seq=1042)

    def test_silence_past_the_watchdog_is_a_connection_fault(self, inbox):
        """
        The other side of the same boundary — and it must be REACHABLE, which it was not:
        with the poll in place the OSError always arrived first, so the silence error could
        never fire in any production configuration.
        """
        server = MockStreamServer([
            MockStreamReply(body=signal_frame(1041)
                            + control('live', stream_epoch=1, head_seq=1041),
                            gap_s=4.0, tail=signal_frame(1042)),
            MockStreamReply(body=signal_frame(1042)
                            + control('live', stream_epoch=1, head_seq=1042), hold_s=0.5),
        ])
        with running(server, inbox, heartbeat_s=0.5, multiple=2.0) as source:
            assert wait_until(lambda: server.get_connection_count() >= 2, timeout_s=8.0)
            stats = source.get_transport_stats()
            assert stats.transport_errors >= 1
            # The fault must be NAMED, and this is the assertion that discriminates: the
            # pre-fix code also reconnected and also counted an error, so a test resting on
            # those two alone passed against the very bug it was written to pin. What only
            # the fixed code produces is the silence error — before, the poisoned socket
            # raised a bare OSError first and the watchdog could never fire at all.
            assert any('SignalStreamSilenceError' in event.message
                       for event in stats.tape), (
                f'the watchdog did not fire; tape says '
                f'{[event.message for event in stats.tape]}')


class TestStoppingWhileTheProducerHangs:
    """A session end must not wait on a producer that stopped answering."""

    @pytest.mark.parametrize('heartbeat_s,multiple,expected_connect', [
        # production shape: watchdog 60 s, so the connect budget is the cap
        (20.0, 3.0, CONNECT_TIMEOUT_S),
        # test shape: the watchdog is the shorter of the two and wins. 0.4 x 2.0 is 0.8,
        # raised to the 1 s floor — settimeout(0) means NON-BLOCKING, so the arithmetic is
        # floored rather than trusted, and this is where that shows.
        (0.4, 2.0, MINIMUM_WATCHDOG_S),
    ])
    def test_the_connect_phase_runs_on_its_own_bounded_budget(
            self, inbox, monkeypatch, heartbeat_s, multiple, expected_connect):
        """
        The one phase nothing can interrupt, so the only defence is that it is SHORT.

        No socket exists until connect() returns, so a stop landing inside it waits the
        connect out. Bounding that at the watchdog meant a session end could hold for a
        minute against an unreachable producer — measured 58 s at the served 20 s
        keep-alive, 9 s once the budget was separated. Never longer than the watchdog
        either: waiting longer to REACH a producer than for one that has gone silent makes
        no sense.

        Asserted on what the transport DOES — the timeout it hands the connection — rather
        than on its private arithmetic.
        """
        seen = []
        real = signal_stream_source.HTTPConnection

        def recording(host, port, timeout=None):
            seen.append(timeout)
            return real(host, port, timeout=timeout)

        monkeypatch.setattr(signal_stream_source, 'HTTPConnection', recording)
        server = MockStreamServer([MockStreamReply(
            body=control('live', stream_epoch=1, head_seq=0), hold_s=0.3)])
        with running(server, inbox, heartbeat_s=heartbeat_s, multiple=multiple):
            assert wait_until(lambda: bool(seen))
        assert seen[0] == expected_connect

    def test_stop_returns_promptly_while_a_connect_is_still_in_flight(self, inbox):
        """
        The hung-upstream case: the connection is ACCEPTED and the response head never
        comes. The socket handle is published before the response is read precisely so
        there is something to shut down — without it a session end blocks for the whole
        watchdog, and in a live session that wait sits AHEAD of closing open positions.
        """
        server = MockStreamServer([MockStreamReply(stall_s=6.0)])
        server.start()
        source = build(server, inbox, heartbeat_s=2.0, multiple=3.0)
        source.start()
        try:
            assert wait_until(lambda: server.get_connection_count() >= 1, timeout_s=4.0)
            started = time.monotonic()
            source.stop()
            elapsed = time.monotonic() - started
        finally:
            server.stop()
        assert elapsed < 2.0, (
            f'stop() waited {elapsed:.1f}s on a hung connect — the whole watchdog is 6s')


class TestTheFrameRecorder:
    """
    What a certificate needs and a parsed object can no longer say (#468, #466).

    Parsing is lossy in exactly the three directions the contract cares about, so a proof
    fed from the inbox would silently lose 60 of the certificate's 97 checks while still
    writing a PASSED artifact — not a false claim, a quieter one, which is harder to see.
    The recorder is how the proof rests on frames the REAL transport delivered rather than
    on a second SSE client written to watch the first.
    """

    def test_off_by_default(self):
        """
        The raw payload is 38 kB an envelope. A session that never certifies must not hold
        it, and a DEFAULT of None is what guarantees that rather than promises it.

        Asserted on the public signature rather than on the instance: reading the private
        attribute from outside would be the §15 violation this suite reports elsewhere.
        """
        default = inspect.signature(
            SignalStreamSource.__init__).parameters['frame_recorder'].default
        assert default is None

    def test_a_session_without_a_recorder_is_unaffected(self, inbox):
        """The optional collaborator is genuinely optional — the frame still flows."""
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041) + control('live', stream_epoch=1, head_seq=1041),
            hold_s=0.4)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            assert source.get_transport_stats().contract_errors == 0

    def test_the_raw_payload_keeps_what_the_parsed_model_loses(self, inbox):
        """
        The three losses, in one frame:

        ABSENCE — `collected_msc` is never on the wire and always on the model, because we
        stamp receipt ourselves. Only the raw mapping can prove the producer did not send it.
        LOCATION — `trigger_reason` belongs at the top level; the reader normalizes an older
        line's `metadata.trigger_reason` upward, so the parsed object cannot say where it
        came from.
        WIRE TYPE — `is_breaking` must be a JSON boolean. Pydantic turns a `1` into `True`
        before anyone can object, so the type check has to run on the raw value.
        """
        recorder = SignalFrameRecorder()
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041) + control('live', stream_epoch=1, head_seq=1041),
            hold_s=0.4)])
        with running(server, inbox, recorder=recorder):
            assert wait_until(lambda: recorder.get_recorded_count() == 1)

        observation = recorder.get_observations()[0]
        assert 'collected_msc' not in observation.envelope, (
            'the wire does not carry it — that is the fact the certificate proves')
        assert observation.snapshot.collected_msc is not None, (
            'and the model always does, which is why the model cannot prove it')
        assert 'trigger_reason' in observation.envelope
        assert 'trigger_reason' not in (observation.envelope.get('metadata') or {})
        assert isinstance(observation.envelope['result'][0]['is_breaking'], bool), (
            'the wire type survives; a coerced 1 would read as True on the model')
        assert observation.frame_bytes > 0

    def test_a_redelivery_is_recorded_because_the_wire_delivered_it(self, inbox):
        """
        Recorded BEFORE the duplicate gate. A certificate asks what the wire did, and a
        redelivery is something the wire did — the series deduplicates, the evidence must not.
        """
        recorder = SignalFrameRecorder()
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041) + signal_frame(1041)
            + control('live', stream_epoch=1, head_seq=1041), hold_s=0.4)])
        with running(server, inbox, recorder=recorder):
            assert wait_until(lambda: recorder.get_recorded_count() == 2)
        assert inbox.get_total_received() == 1, 'the SERIES still deduplicates'


class TestResilience:
    """What the socket does when nobody is watching."""

    def test_a_silent_socket_is_a_connection_fault_and_reconnects(self, inbox):
        """
        The watchdog is the served keep-alive interval times our multiple, and it says
        nothing about freshness — a stalled seq is the provider's business, a dead socket
        is ours.
        """
        server = MockStreamServer([
            MockStreamReply(body=b'', hold_s=2.0),
            MockStreamReply(body=signal_frame(1041)
                            + control('live', stream_epoch=1, head_seq=1041), hold_s=0.5),
        ])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1, timeout_s=6.0)
            assert source.get_transport_stats().transport_errors >= 1

    def test_a_server_error_backs_off_and_retries(self, inbox):
        """
        The mirror of the refusals: 503 is THEIR problem and reconnecting is exactly the
        right answer, so it must not stop the transport the way a 404 does.
        """
        server = MockStreamServer([
            MockStreamReply(status=503),
            MockStreamReply(body=signal_frame(1041)
                            + control('live', stream_epoch=1, head_seq=1041), hold_s=0.5),
        ])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1, timeout_s=5.0)
            assert source.get_transport_stats().transport_errors == 1

    def test_a_closed_connection_reconnects_without_losing_the_cursor(self, inbox):
        server = MockStreamServer([
            MockStreamReply(body=signal_frame(1041)
                            + control('live', stream_epoch=1, head_seq=1041)),
            MockStreamReply(body=signal_frame(1042)
                            + control('live', stream_epoch=1, head_seq=1042), hold_s=0.5),
        ])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 2, timeout_s=5.0)
            assert source.get_cursor() == SignalStreamCursor(epoch=1, seq=1042)
        assert server.get_queries()[1] == f'/v1/stream/{PIPELINE}?since=1041&epoch=1'

    def test_an_unreadable_envelope_does_not_drop_the_connection(self, inbox):
        """
        They answered; we could not read it. Counted apart from a transport error on
        purpose — blaming their infrastructure for our schema is a diagnosis sent to the
        wrong system, and dropping the connection retries a mismatch retrying cannot fix.
        """
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041, available_msc='not-a-timestamp')
            + signal_frame(1042) + control('live', stream_epoch=1, head_seq=1042),
            hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            stats = source.get_transport_stats()
            assert stats.contract_errors == 1
            assert stats.transport_errors == 0
            assert server.get_connection_count() == 1

    def test_an_unsupported_schema_major_is_refused_not_guessed(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=signal_frame(1041, schema_version='9.0')
            + control('live', stream_epoch=1, head_seq=1041), hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(
                lambda: source.get_transport_stats().contract_errors == 1)
            assert inbox.get_total_received() == 0

    def test_an_undecodable_frame_is_a_contract_error(self, inbox):
        server = MockStreamServer([MockStreamReply(
            body=b'event: signal\ndata: {not json}\n\n'
            + signal_frame(1041) + control('live', stream_epoch=1, head_seq=1041),
            hold_s=0.5)])
        with running(server, inbox) as source:
            assert wait_until(lambda: inbox.get_total_received() == 1)
            assert source.get_transport_stats().contract_errors == 1
