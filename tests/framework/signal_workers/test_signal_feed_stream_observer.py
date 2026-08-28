"""
FiniexTestingIDE - Signal Feed Stream Observer (#468, #466)

The certificate's reader over the push transport. Runs UNMOCKED against a local producer
that serves the same four routes the real one does, the way the poll suite runs against a
real socket — patching the reads would skip exactly what breaks against a real server.

What is pinned is what a certificate must be able to say afterwards. Two of those are the
defects this observer exists to remove:

- the transport is RECORDED from the run. It used to be a module constant written straight
  into the artifact, so a certificate taken over the stream would have claimed `poll`;
- the RAW envelope survives. Roughly two thirds of the certificate's checks read the wire
  and not the parsed model — a field's absence, its wire type and its location are all
  unanswerable once a payload has become an object.
"""

import json
from unittest.mock import MagicMock

import pytest

from python.framework.signal_data.producer.signal_feed_stream_observer import (
    SignalFeedStreamObserver,
)
from python.framework.types.config_types.sentiment_config_types import (
    ActiveProducer,
    ResolvedCredential,
    SentimentStreamConfig,
)
from python.framework.types.signal_data_types import SignalTransportKind
from tests.framework.signal_workers.conftest import MockStreamReply, MockStreamServer

PIPELINE = 'crypto_sentiment'
TOKEN = 'test-token'
BASE_MSC = 1787312488624


def envelope(seq: int, epoch: int = 1) -> dict:
    """One envelope in the shape the stream carries."""
    return {
        'schema_version': '2.0', 'pipeline_id': PIPELINE, 'seq': seq,
        'stream_epoch': epoch, 'available_msc': BASE_MSC + seq * 600_000,
        'trigger_reason': 'scheduled', 'data_origin': 'live', 'status': 'success',
        'result': [{'symbol': 'BTCUSD', 'signal': 'BUY', 'sentiment_score': 0.8,
                    'confidence': 0.9, 'basis': 'llm',
                    'is_breaking': False, 'urgency': 0.0}],
    }


def frame(event: str, payload: dict) -> bytes:
    """One SSE frame, the way the producer writes it."""
    return f'event: {event}\ndata: {json.dumps(payload)}\n\n'.encode('utf-8')


def three_envelopes() -> bytes:
    """A connect snapshot carrying three envelopes, then the live marker."""
    body = b''.join(frame('signal', envelope(seq)) for seq in (938, 939, 940))
    return body + frame('control', {'code': 'live', 'stream_epoch': 1, 'head_seq': 940})


def held(body: bytes, hold_s: float = 1.5) -> MockStreamReply:
    """
    One reply that KEEPS the connection open after its body.

    A real stream stays open between envelopes; a mock that closes after writing makes the
    transport reconnect and deliver the same snapshot again, which is a property of the
    test rig rather than of the producer. Without the hold, a 0.6 s observation collected
    the same three envelopes sixteen times over.
    """
    return MockStreamReply(body=body, hold_s=hold_s)


def observe(server: MockStreamServer, seconds: float = 0.6, pipeline_id: str = PIPELINE,
            token: str = TOKEN):
    """Run one observation against a started server and stop it afterwards."""
    server.start()
    try:
        return SignalFeedStreamObserver(
            producer=ActiveProducer(
                name='test', base_url=server.base_url(),
                credential=ResolvedCredential(token=token, source='tests (in-memory)')),
            stream_config=SentimentStreamConfig(
                enabled=True, pipeline_id=pipeline_id, heartbeat_timeout_multiple=2.0,
                reconnect_backoff_initial_s=0.05, reconnect_backoff_max_s=0.1),
            logger=MagicMock(),
        ).observe(seconds=seconds)
    finally:
        server.stop()


class TestWhatTheCertificateGetsBack:
    """The two facts an artifact could not previously establish."""

    def test_the_transport_is_recorded_from_the_run(self):
        """
        Not declared. This was a module constant until 2026-08-28, so a certificate taken
        over the stream would have written `poll` into its own artifact — the same defect
        as an adapter certificate that re-read a config file instead of recording what its
        run did.
        """
        probe = observe(MockStreamServer([held(three_envelopes())]))
        assert probe.transport is SignalTransportKind.STREAM

    def test_the_raw_envelope_survives_beside_its_parsed_form(self):
        """
        The reason the transport carries a frame recorder at all. `collected_msc` is never
        on the wire and always on the model, so only the raw mapping can prove the producer
        did not send it — and roughly two thirds of the certificate's checks read the wire.
        """
        probe = observe(MockStreamServer([held(three_envelopes())]))
        assert len(probe.observations) == 3
        for observation in probe.observations:
            assert 'collected_msc' not in observation.envelope
            assert observation.snapshot.collected_msc is not None
            assert observation.frame_bytes > 0

    def test_three_envelopes_are_asked_for_so_a_series_can_be_judged(self):
        """
        One position cannot show that a series MOVED — the comparison loop in the validator
        runs zero times and the check would pass while proving nothing. The connect asks for
        several, which their contract offers as `history=N`; waiting for a second envelope
        would mean holding the connection past a ten-minute cadence for a fact the snapshot
        can carry at once.
        """
        server = MockStreamServer([held(three_envelopes())])
        probe = observe(server)
        assert [o.snapshot.seq for o in probe.observations] == [938, 939, 940]
        assert 'history=3' in server.get_queries()[0]

    def test_all_four_free_routes_are_recorded(self):
        """
        The route list is what lets a later reader see the run spent nothing. `/v1/pipelines`
        is owed structurally rather than for completeness: the transport cannot start
        without the keep-alive interval its watchdog measures against.
        """
        probe = observe(MockStreamServer([held(three_envelopes())]))
        assert [(c.method, c.path) for c in probe.routes_used] == [
            ('GET', '/v1/health'),
            ('GET', '/v1/build'),
            ('GET', '/v1/pipelines'),
            ('GET', f'/v1/stream/{PIPELINE}'),
        ]

    def test_the_producer_identity_is_read_without_a_token(self):
        probe = observe(MockStreamServer([held(three_envelopes())]))
        assert probe.identity.journal_id == 'testjournal01'
        assert probe.identity.cadence_seconds == 600
        assert probe.build.offered is True


class TestWhenTheRunCannotProceed:
    """Each refusal names what a session would have hit, and none of them passes quietly."""

    def test_a_producer_that_does_not_serve_the_stream_block_is_refused(self):
        """
        The watchdog would have no interval to measure against, so a session could not open
        a stream either. Recorded as a failure rather than defaulted to a plausible number.
        """
        server = MockStreamServer(
            [held(three_envelopes())],
            registry={'pipelines': [{'pipeline_id': PIPELINE, 'cadence_seconds': 600}]})
        probe = observe(server)
        assert not probe.observations
        assert 'stream_settings_served' in {f.name for f in probe.transport_failures}

    def test_an_unregistered_pipeline_is_refused_and_names_what_exists(self):
        server = MockStreamServer(
            [held(three_envelopes())],
            registry={'stream': {'heartbeat_seconds': 20, 'replay_window_hours': 24},
                      'pipelines': [{'pipeline_id': 'something_else',
                                     'cadence_seconds': 600}]})
        probe = observe(server)
        failure = next(f for f in probe.transport_failures
                       if f.name == 'pipeline_registered')
        assert 'something_else' in failure.detail

    def test_a_rejected_credential_is_reported_as_a_credential_condition(self):
        """
        Never as unreachability. The producer answered correctly; we are the ones without a
        valid token, and sending an operator to the wrong system is what this distinction
        exists to prevent.
        """
        probe = observe(MockStreamServer([held(three_envelopes())]),
                        token='wrong')
        assert 'credential_accepted' in {f.name for f in probe.transport_failures}

    def test_a_stream_that_delivers_nothing_is_a_failure_and_not_a_silence(self):
        """
        The connection opened and no envelope came. Left unreported, a certificate would
        have run its whole check list over an empty observation set — which is the shape
        that passes while proving nothing.
        """
        probe = observe(MockStreamServer([MockStreamReply(body=b'', hold_s=0.4)]),
                        seconds=0.8)
        assert not probe.observations
        assert 'stream_delivered_an_envelope' in {f.name for f in probe.transport_failures}

    def test_a_frame_our_reader_refuses_is_reported_as_a_contract_error(self):
        """
        They answered; our schema could not read it. Counted apart from a transport fault,
        because blaming their infrastructure for our reader is a diagnosis sent to the
        wrong system.
        """
        broken = dict(envelope(938))
        broken['available_msc'] = 'not-a-timestamp'
        body = (frame('signal', broken) + frame('signal', envelope(939))
                + frame('control', {'code': 'live', 'stream_epoch': 1, 'head_seq': 939}))
        probe = observe(MockStreamServer([held(body)]))
        names = {f.name for f in probe.transport_failures}
        assert 'every_frame_parses_through_production_reader' in names
        assert [o.snapshot.seq for o in probe.observations] == [939], (
            'the readable frame still counts — one bad envelope is not a dead feed')


@pytest.mark.parametrize('missing', ['health', 'build'])
def test_a_free_route_that_does_not_answer_does_not_stop_the_observation(missing):
    """
    Identity and build are read without a token and neither gates the stream. `/v1/build`
    especially: it sits behind a switch on the producer's side, so its absence is a POLICY
    answer and a certificate that failed on their configuration choice would be asserting
    something nobody promised.
    """
    server = MockStreamServer([held(three_envelopes())],
                              health=None if missing == 'build' else {})
    probe = observe(server)
    assert len(probe.observations) == 3, 'the stream half is independent of these two'
