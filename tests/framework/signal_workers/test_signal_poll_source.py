"""
Interim signal poll transport (#141 Part 2a): the pull path used until the producer's stream exists.

Runs against a local stub, never against a real producer — a suite that needs someone else's
container to be running is a suite that fails for reasons unrelated to the code.

The behaviour worth pinning is mostly about restraint: the producer republishes the same stored
envelope until its next pass, and its degraded answer must never be mistaken for a signal.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import pytest

from python.framework.signal_data.signal_inbox import SignalInbox
from python.framework.signal_data.signal_poll_source import SignalPollSource
from python.framework.types.config_types.sentiment_config_types import SentimentPollConfig

SIGNAL_KIND = 'llm_sentiment'


def envelope(seq: int, stream_epoch: int = 1) -> dict:
    """A stored envelope in the shape the producer serves."""
    base = 1787312488624
    return {
        'schema_version': '2.0',
        'pipeline_id': 'crypto_sentiment',
        'seq': seq,
        'stream_epoch': stream_epoch,
        'available_msc': base + seq * 600_000,
        'trigger_reason': 'scheduled',
        'data_origin': 'live',
        'status': 'success',
        'result': [{'symbol': 'BTCUSD', 'signal': 'BUY', 'sentiment_score': 0.8,
                    'confidence': 0.9, 'basis': 'llm'}],
    }


def store_unavailable() -> dict:
    """The producer's answer when it cannot serve from its store — never a paid run."""
    return {
        'schema_version': '2.0',
        'pipeline_id': 'crypto_sentiment',
        'status': 'error',
        'result': [],
        'errors': [{'type': 'VECTOR_STORE_ERROR',
                    'message': 'no outcome persisted yet for this pipeline'}],
    }


class _Stub:
    """A local HTTP stub serving a scripted sequence of responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):                      # noqa: N802 — BaseHTTPRequestHandler API
                stub.requests.append(dict(self.headers))
                payload = stub.responses[min(len(stub.requests) - 1,
                                             len(stub.responses) - 1)]
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):          # noqa: A003 — silence the stub
                pass

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def base_url(self) -> str:
        """Base URL of the running stub."""
        return f'http://127.0.0.1:{self.server.server_port}'


def build(stub, inbox, token: str = '') -> SignalPollSource:
    """A poll source pointed at the stub."""
    return SignalPollSource(
        config=SentimentPollConfig(
            enabled=True, base_url=stub.base_url, pipeline_id='crypto_sentiment',
            interval_s=0.05, request_timeout_s=3.0, degraded_backoff_s=0.05),
        signal_kind=SIGNAL_KIND, inbox=inbox, logger=MagicMock(), api_token=token)


class TestArrival:
    """What reaches the inbox."""

    def test_new_envelope_is_enqueued(self):
        inbox = SignalInbox()
        with _Stub([envelope(23)]) as stub:
            build(stub, inbox)._poll_once()
        drained = inbox.drain()
        assert len(drained[SIGNAL_KIND]) == 1
        assert drained[SIGNAL_KIND][0].seq == 23

    def test_receipt_is_stamped_by_us(self):
        """collected_msc is absent on the wire — the consumer supplies it."""
        inbox = SignalInbox()
        with _Stub([envelope(23)]) as stub:
            build(stub, inbox)._poll_once()
        snapshot = inbox.drain()[SIGNAL_KIND][0]
        assert snapshot.collected_msc is not None
        # The gate stays the producer's availability stamp, not our receipt.
        assert snapshot.get_resolution_key() == snapshot.available_msc

    def test_same_envelope_is_not_enqueued_twice(self):
        """The producer republishes the stored envelope until its next pass."""
        inbox = SignalInbox()
        with _Stub([envelope(23)]) as stub:
            source = build(stub, inbox)
            source._poll_once()
            source._poll_once()
            source._poll_once()
        assert len(inbox.drain()[SIGNAL_KIND]) == 1
        assert source.get_stats() == (3, 1, 0)

    def test_next_pass_is_enqueued(self):
        inbox = SignalInbox()
        with _Stub([envelope(23), envelope(24)]) as stub:
            source = build(stub, inbox)
            source._poll_once()
            source._poll_once()
        assert [s.seq for s in inbox.drain()[SIGNAL_KIND]] == [23, 24]


class TestDegradedProducer:
    """The producer's store answer is a transport condition, never a signal."""

    def test_store_unavailable_is_not_enqueued(self):
        """
        Enqueuing it would put a degraded HOLD into the series, which the provider would
        later resolve as if it were sentiment. These envelopes are also never persisted on
        the producer side, so they carry no seq and never appear on the stream.
        """
        inbox = SignalInbox()
        with _Stub([store_unavailable()]) as stub:
            source = build(stub, inbox)
            assert source._poll_once() is True
        assert inbox.drain() == {}
        assert source.get_stats() == (1, 0, 1)

    def test_recovery_after_degraded(self):
        inbox = SignalInbox()
        with _Stub([store_unavailable(), envelope(23)]) as stub:
            source = build(stub, inbox)
            source._poll_once()
            source._poll_once()
        assert len(inbox.drain()[SIGNAL_KIND]) == 1
        assert source.get_stats() == (2, 1, 1)

    def test_a_normal_error_envelope_is_still_a_signal(self):
        """
        status='error' alone is a PRODUCER failure (an LLM timeout), which is real data:
        it resolves to a defensive HOLD. Only VECTOR_STORE_ERROR means "no envelope".
        """
        payload = {**envelope(23), 'status': 'error', 'result': [],
                   'errors': [{'type': 'LLM_TIMEOUT', 'message': 'upstream timeout'}]}
        inbox = SignalInbox()
        with _Stub([payload]) as stub:
            source = build(stub, inbox)
            assert source._poll_once() is False
        assert len(inbox.drain()[SIGNAL_KIND]) == 1


class TestAuth:
    """The token is sent only when there is one."""

    def test_no_header_without_a_token(self):
        with _Stub([envelope(23)]) as stub:
            build(stub, SignalInbox())._poll_once()
            assert 'Authorization' not in stub.requests[0]

    def test_bearer_header_with_a_token(self):
        with _Stub([envelope(23)]) as stub:
            build(stub, SignalInbox(), token='s3cret')._poll_once()
            assert stub.requests[0]['Authorization'] == 'Bearer s3cret'


class TestLifecycle:
    """A transport fault must never take the session with it."""

    def test_unreachable_producer_does_not_raise(self):
        """The thread logs and retries; the loop never learns about it."""
        inbox = SignalInbox()
        source = SignalPollSource(
            config=SentimentPollConfig(
                enabled=True, base_url='http://127.0.0.1:9', pipeline_id='crypto_sentiment',
                interval_s=0.05, request_timeout_s=1.0),
            signal_kind=SIGNAL_KIND, inbox=inbox, logger=MagicMock())
        source.start()
        source.stop()
        assert inbox.drain() == {}

    def test_start_stop_collects_envelopes(self):
        inbox = SignalInbox()
        with _Stub([envelope(23)]) as stub:
            source = build(stub, inbox)
            source.start()
            source.stop()
        polls, enqueued, degraded = source.get_stats()
        assert polls >= 1 and enqueued == 1 and degraded == 0

    def test_stop_is_idempotent(self):
        with _Stub([envelope(23)]) as stub:
            source = build(stub, SignalInbox())
            source.start()
            source.stop()
            source.stop()
