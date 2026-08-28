"""
FiniexTestingIDE - Signal Workers Test Fixtures
Shared helpers for the SIGNAL worker / provider / hybrid-decision tests (#141).

No tick loop, no batch. Builds SignalSeries / providers / workers in-process and
injects them directly (the same seam the framework uses at construction).
"""

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.signal_data_types import (
    RunError,
    SentimentResult,
    SignalSeries,
    SignalSnapshot,
)

SYMBOL = 'BTCUSD'

# tests/fixtures/signals/sentiment_sample.jsonl
FIXTURE_JSONL = (
    Path(__file__).resolve().parents[2] / 'fixtures' / 'signals' / 'sentiment_sample.jsonl'
)


@pytest.fixture(scope='session')
def mock_logger():
    """Minimal mock logger for worker / decision-logic instantiation."""
    return MagicMock()


def utc(year, month, day, hour, minute, second=0) -> datetime:
    """Build a UTC, tz-aware datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def make_tick(timestamp: datetime, symbol: str = SYMBOL, mid: float = 100.0) -> TickData:
    """Build a minimal tick at a timestamp."""
    return TickData(timestamp=timestamp, symbol=symbol, bid=mid, ask=mid + 0.02, volume=0.1)


def snapshot(
    collected_msc: datetime,
    score: float,
    confidence: float,
    signal: str = 'HOLD',
    urgency: float = 0.0,
    is_breaking: bool = False,
    symbol: str = SYMBOL,
    status: str = 'success',
) -> SignalSnapshot:
    """Build one SignalSnapshot carrying a single per-symbol result."""
    return SignalSnapshot(
        collected_msc=collected_msc,
        schema_version='1.0',
        status=status,
        result=[SentimentResult(
            symbol=symbol, signal=signal, sentiment_score=score,
            confidence=confidence, urgency=urgency, is_breaking=is_breaking,
        )],
    )


def error_snapshot(collected_msc: datetime) -> SignalSnapshot:
    """Build a status='error' snapshot with empty result (no usable sentiment)."""
    return SignalSnapshot(
        collected_msc=collected_msc, schema_version='1.0', status='error',
        result=[], errors=[RunError(type='LLM_TIMEOUT')],
    )


def make_provider(*snapshots: SignalSnapshot) -> SignalDataProvider:
    """Build a provider over the given snapshots (any order)."""
    return SignalDataProvider(
        SignalSeries(signal_kind='llm_sentiment', snapshots=list(snapshots))
    )


@dataclass
class MockStreamReply:
    """
    One connection's worth of scripted stream behaviour (#468).

    A list of these is a script: the first connection gets the first reply, a reconnect the
    next, and the last one repeats. That is what makes reconnect, gap replay and epoch
    change expressible as tests rather than as timing luck.

    Args:
        body: Raw SSE bytes written to the connection, then the connection closes
        status: HTTP status — a non-200 is sent instead of a body
        hold_s: How long to keep the connection open and SILENT after the body, which is
            the only way to exercise a connection watchdog
        gap_s: Silence in the MIDDLE of a connection, before `tail` is written. The shape a
            real producer has between keep-alives, and the one a reply that writes
            everything at once can never produce
        tail: Bytes written after `gap_s`, on the same connection
        stall_s: Silence BEFORE the response head, with the connection already accepted —
            the hung-upstream case, where the client is blocked inside getresponse()
    """
    body: bytes = b''
    status: int = 200
    hold_s: float = 0.0
    gap_s: float = 0.0
    tail: bytes = b''
    stall_s: float = 0.0


class MockStreamServer:
    """
    A local SSE server standing in for the producer's stream.

    Enforces the parts of the connect contract a client can get wrong — bearer auth, an
    unknown pipeline as 404, `history` and `since` as mutually exclusive, `since` without
    `epoch` as 400 — because a mock that accepts everything cannot catch the request being
    built wrong, which is the most likely defect in a transport.
    """

    def __init__(
        self,
        replies: List[MockStreamReply],
        pipeline_id: str = 'crypto_sentiment',
        token: str = 'test-token',
        registry: Optional[dict] = None,
        health: Optional[dict] = None,
    ):
        """
        Initialize the server.

        Args:
            replies: Scripted replies, one per connection; the last repeats
            pipeline_id: The only pipeline this server knows — anything else is a 404
            token: Bearer token it accepts; anything else is a 401
            registry: What `GET /v1/pipelines` answers; None serves a well-formed default.
                Given explicitly, a test can withhold the engine-wide stream block or the
                pipeline itself and see what the reader does with that
            health: What `GET /v1/health` answers; None serves a well-formed default
        """
        self._replies = list(replies)
        self._pipeline_id = pipeline_id
        self._token = token
        self._registry = registry if registry is not None else {
            'stream': {'heartbeat_seconds': 20, 'replay_window_hours': 24},
            'pipelines': [{'pipeline_id': pipeline_id, 'cadence_seconds': 600}],
        }
        self._health = health if health is not None else {
            'journal_id': 'testjournal01', 'environment': 'test', 'version': '0.0.1',
            'pass_timeout_seconds': 300,
            'workers': [{'name': f'eval:{pipeline_id}', 'interval_seconds': 600}],
        }
        self._queries: List[str] = []
        self._connections = 0
        self._lock = threading.Lock()
        # Threading, so a handler still sleeping out its hold does not make shutdown
        # wait for it — with one thread per connection the suite pays each hold once, in
        # parallel, instead of serially at teardown.
        self._httpd = ThreadingHTTPServer(('127.0.0.1', 0), self._build_handler())
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> 'MockStreamServer':
        """
        Begin serving.

        Returns:
            Itself, so a test can start and bind in one line
        """
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop serving and release the port."""
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2.0)

    def base_url(self) -> str:
        """
        Address the transport should be pointed at.

        Returns:
            The server root, without a trailing slash
        """
        return f'http://127.0.0.1:{self._httpd.server_address[1]}'

    def get_queries(self) -> List[str]:
        """
        Every request line received, in order.

        Returns:
            The raw paths including their query strings
        """
        with self._lock:
            return list(self._queries)

    def get_token(self) -> str:
        """
        The bearer token this server accepts.

        Returns:
            The token
        """
        return self._token

    def get_registry(self) -> dict:
        """
        What `GET /v1/pipelines` answers.

        Returns:
            The registry document
        """
        return self._registry

    def get_health(self) -> dict:
        """
        What `GET /v1/health` answers.

        Returns:
            The health document
        """
        return self._health

    def get_pipeline_id(self) -> str:
        """
        The only pipeline this server knows.

        Returns:
            The pipeline id
        """
        return self._pipeline_id

    def get_connection_count(self) -> int:
        """
        How many connections were opened.

        Returns:
            The count
        """
        with self._lock:
            return self._connections

    def take_reply(self, path: str) -> MockStreamReply:
        """
        Record one connection and take the reply scripted for it.

        Args:
            path: The request line, recorded for assertions

        Returns:
            The scripted reply; the last one repeats once the script runs out
        """
        with self._lock:
            index = self._connections
            self._connections += 1
            self._queries.append(path)
        if not self._replies:
            return MockStreamReply()
        return self._replies[min(index, len(self._replies) - 1)]

    def _build_handler(self):
        """
        Build the request handler bound to this server instance.

        Returns:
            The handler class
        """
        server = self

        class Handler(BaseHTTPRequestHandler):
            """Serves the scripted stream, enforcing the connect contract."""

            protocol_version = 'HTTP/1.0'

            def log_message(self, *args):
                """Silence the default stderr logging."""

            def do_GET(self):     # noqa: N802 — BaseHTTPRequestHandler's own naming
                """Answer one request — the stream, or one of the producer's free routes."""
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)

                # The three routes a certificate run reads before it opens the stream.
                # Served here rather than mocked so the observer runs unmocked end to end,
                # the way the poll suite runs against a real socket.
                if parsed.path == '/v1/health':
                    return self._json(server.get_health())
                if parsed.path == '/v1/build':
                    return self._json({'version': '0.0.1', 'commit': 'abc123def456',
                                       'dirty': False})
                if parsed.path == '/v1/pipelines':
                    if self.headers.get('Authorization') != f'Bearer {server.get_token()}':
                        return self.send_error(401)
                    return self._json(server.get_registry())

                reply = server.take_reply(self.path)

                fault = self._contract_fault(parsed.path, params)
                if fault is not None:
                    self.send_error(fault)
                    return
                if reply.status != 200:
                    self.send_error(reply.status)
                    return

                if reply.stall_s:
                    time.sleep(reply.stall_s)

                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self._write(reply.body)
                if reply.gap_s:
                    time.sleep(reply.gap_s)
                self._write(reply.tail)
                if reply.hold_s:
                    time.sleep(reply.hold_s)

            def _json(self, payload):
                """
                Answer one JSON route.

                Args:
                    payload: The object to serialize
                """
                body = json.dumps(payload).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _write(self, payload: bytes):
                """
                Write one batch of frames and flush, tolerating a client that left.

                Args:
                    payload: Bytes to write; empty writes nothing
                """
                if not payload:
                    return
                try:
                    self.wfile.write(payload)
                    self.wfile.flush()
                except OSError:
                    pass   # the transport shut the socket down, which is a test's right

            def _contract_fault(self, path: str, params: dict):
                """
                The status this request should be refused with, if any.

                Args:
                    path: Request path without the query
                    params: Decoded query parameters

                Returns:
                    The status to refuse with, or None when the request is well formed
                """
                header = self.headers.get('Authorization', '')
                if header != f'Bearer {server.get_token()}':
                    return 401
                if path != f'/v1/stream/{server.get_pipeline_id()}':
                    return 404
                if 'history' in params and 'since' in params:
                    return 400
                if ('since' in params) != ('epoch' in params):
                    return 400
                return None

        return Handler
