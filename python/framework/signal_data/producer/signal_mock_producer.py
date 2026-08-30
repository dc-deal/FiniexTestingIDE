"""
FiniexTestingIDE - Signal Mock Producer
A local stand-in for the producer, so the transport's own surface can be looked at (#468).

Exists for one gap and no more. A mock AutoTrader session mounts its signal series from the
archive, which is what makes a replay reproducible — so it opens no connection at all, and
every mock run in this project therefore exercises everything BEHIND the inbox and nothing
in front of it. The transport's failure surface, the five control codes above all, is
reachable only from a real connection.

Four of those codes a healthy producer will never emit on request. This server emits them
on demand, over a real socket, against the unmodified transport: same frames, same parsing,
same routing, same operator lines. It is a diagnostic tool and never part of a session —
nothing in the runtime path imports it.

The envelopes are deliberately thin. What is being looked at is the transport, not the
payload; the payload only has to be something the production model accepts.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from python.framework.types.signal_data_types import (
    SignalStreamControlCode,
    SignalStreamEventName,
)

# What the served registry reports. The keep-alive is far below the producer's 20 s so a
# short look already crosses several beats; the replay window matches theirs.
MOCK_HEARTBEAT_SECONDS = 2.0
MOCK_REPLAY_WINDOW_HOURS = 24.0
MOCK_CADENCE_SECONDS = 600.0
MOCK_JOURNAL_ID = 'mock00000000'
MOCK_PIPELINE_ID = 'crypto_sentiment'
# Envelopes served before the stream goes live, and the sequence they start at.
MOCK_SNAPSHOT_COUNT = 3
MOCK_FIRST_SEQ = 1041
MOCK_EPOCH = 1
# Seconds after `control/live` before an injected code is emitted, so the operator sees the
# healthy stream first and the diagnosis second — the order a real session would show.
INJECT_AFTER_SECONDS = 4.0


class SignalMockProducer:
    """
    Serves the producer's four free routes on localhost, with a scriptable control code.

    Started and stopped by the caller; it holds no state a second run would inherit.
    """

    def __init__(
        self,
        pipeline_id: str = MOCK_PIPELINE_ID,
        inject: Optional[SignalStreamControlCode] = None,
        inject_after_seconds: float = INJECT_AFTER_SECONDS,
    ):
        """
        Initialize the stand-in.

        Args:
            pipeline_id: Source name the registry and the stream answer for
            inject: Control code emitted after the stream has gone live, or None for a
                healthy stream that only ever sends envelopes and keep-alives
            inject_after_seconds: Delay before the code is emitted. The default is paced
                for a human watching; a test shortens it
        """
        self._pipeline_id = pipeline_id
        self._inject = inject
        self._inject_after = inject_after_seconds
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> 'SignalMockProducer':
        """
        Bind a port and serve until stopped.

        Returns:
            Itself, so a caller can start and keep it in one expression
        """
        self._httpd = ThreadingHTTPServer(('127.0.0.1', 0), self._build_handler())
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop serving and release the port."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def get_base_url(self) -> str:
        """
        The address this instance is reachable at.

        Returns:
            Base URL including the bound port
        """
        if self._httpd is None:
            raise RuntimeError('the mock producer is not running')
        return f'http://127.0.0.1:{self._httpd.server_address[1]}'

    def get_inject(self) -> Optional[SignalStreamControlCode]:
        """
        The control code this instance emits once the stream is live.

        Returns:
            The code, or None for a healthy stream
        """
        return self._inject

    def get_inject_after_seconds(self) -> float:
        """
        How long the stream stays healthy before the injected code arrives.

        Returns:
            The delay in seconds
        """
        return self._inject_after

    def get_pipeline_id(self) -> str:
        """
        The source this instance answers for.

        Returns:
            The pipeline id
        """
        return self._pipeline_id

    # ============================================
    # Internals
    # ============================================

    def build_envelope(self, seq: int, produced_at: datetime) -> dict:
        """
        One envelope in the shape the production reader accepts.

        Args:
            seq: Sequence number to stamp
            produced_at: Availability moment of this envelope

        Returns:
            The envelope as it goes on the wire — no `collected_msc`, which the consumer
            stamps on receipt
        """
        return {
            'schema_version': '2.0',
            'pipeline_id': self._pipeline_id,
            'outcome_type': 'sentiment',
            'seq': seq,
            'stream_epoch': MOCK_EPOCH,
            'trigger_reason': 'scheduled',
            'data_origin': 'synthetic',
            'prompt_version': 'mock',
            'config_fingerprint': 'mock00000000',
            'available_msc': int(produced_at.timestamp() * 1000),
            'status': 'success',
            'result': [],
        }

    def build_control(self, code: SignalStreamControlCode, head_seq: int) -> dict:
        """
        One control payload, filled with the fields its code carries.

        Args:
            code: The control code to build
            head_seq: Newest sequence this server claims

        Returns:
            The payload
        """
        payload = {'code': code.value, 'stream_epoch': MOCK_EPOCH}
        if code is SignalStreamControlCode.LIVE:
            payload['head_seq'] = head_seq
        elif code is SignalStreamControlCode.REPLAY_TRUNCATED:
            payload.update({'requested_since': MOCK_FIRST_SEQ - 200,
                            'oldest_available_seq': MOCK_FIRST_SEQ,
                            'window_hours': MOCK_REPLAY_WINDOW_HOURS})
        elif code is SignalStreamControlCode.CURSOR_AHEAD:
            payload.update({'requested_since': head_seq + 7958, 'head_seq': head_seq})
        elif code is SignalStreamControlCode.EPOCH_CHANGED:
            payload.update({'stream_epoch': MOCK_EPOCH + 1, 'previous_epoch': MOCK_EPOCH,
                            'head_seq': head_seq})
        elif code is SignalStreamControlCode.AUTH_REVOKED:
            payload['detail'] = 'token revoked by the mock producer'
        return payload

    def _build_handler(self):
        """
        The request handler class, closed over this instance.

        Returns:
            A BaseHTTPRequestHandler subclass
        """
        server = self

        class Handler(BaseHTTPRequestHandler):
            """Serves the four free routes; anything else is a 404."""

            def log_message(self, *args):
                """Silence the default stderr access log."""

            def do_GET(self):     # noqa: N802 — BaseHTTPRequestHandler's own naming
                """Route one request."""
                path = self.path.split('?')[0]
                if path == '/v1/health':
                    self._json({'status': 'ok', 'journal_id': MOCK_JOURNAL_ID,
                                'environment': 'mock', 'service': 'signal-mock-producer',
                                'pass_timeout_seconds': 300, 'workers': {}})
                elif path == '/v1/build':
                    self._json({'version': '0.0.0-mock', 'commit': 'mock000',
                                'committed_at': '2026-01-01T00:00:00Z', 'dirty': False,
                                'started_at': datetime.now(timezone.utc).isoformat()})
                elif path == '/v1/pipelines':
                    self._json({
                        'stream': {'heartbeat_seconds': MOCK_HEARTBEAT_SECONDS,
                                   'replay_window_hours': MOCK_REPLAY_WINDOW_HOURS},
                        'pipelines': [{'pipeline_id': server.get_pipeline_id(),
                                       'outcome_type': 'sentiment',
                                       'trigger_type': 'scheduled',
                                       'cadence_seconds': MOCK_CADENCE_SECONDS}]})
                elif path == f'/v1/stream/{server.get_pipeline_id()}':
                    self._stream()
                else:
                    self.send_error(404)

            def _json(self, payload: dict) -> None:
                """
                Answer one JSON route.

                Args:
                    payload: What to serialize
                """
                body = json.dumps(payload).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _frame(self, event: SignalStreamEventName, payload: dict) -> None:
                """
                Write one SSE frame and flush it, so it arrives as its own event.

                Args:
                    event: Frame name
                    payload: The `data:` line's content
                """
                block = f'event: {event.value}\ndata: {json.dumps(payload)}\n\n'
                self.wfile.write(block.encode('utf-8'))
                self.wfile.flush()

            def _stream(self) -> None:
                """Serve the snapshot, go live, keep alive, and inject on request."""
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(b'retry: 5000\n\n')

                now = datetime.now(timezone.utc)
                head = MOCK_FIRST_SEQ + MOCK_SNAPSHOT_COUNT - 1
                if server.get_inject() is SignalStreamControlCode.REPLAY_TRUNCATED:
                    self._frame(SignalStreamEventName.CONTROL, server.build_control(
                        SignalStreamControlCode.REPLAY_TRUNCATED, head))
                for index in range(MOCK_SNAPSHOT_COUNT):
                    seq = MOCK_FIRST_SEQ + index
                    produced = now - timedelta(
                        seconds=(MOCK_SNAPSHOT_COUNT - index) * MOCK_CADENCE_SECONDS)
                    self._frame(SignalStreamEventName.SIGNAL, server.build_envelope(seq, produced))
                self._frame(SignalStreamEventName.CONTROL, server.build_control(
                    SignalStreamControlCode.LIVE, head))

                # Keep-alives until the injection moment, then the code under inspection.
                # A terminal code ends the response, which is what their server does.
                waited = 0.0
                while True:
                    beat = MOCK_HEARTBEAT_SECONDS
                    try:
                        time.sleep(beat)
                    except Exception:      # noqa: BLE001 — a stopped server is not an error
                        return
                    waited += beat
                    inject = server.get_inject()
                    if (inject is not None
                            and inject is not SignalStreamControlCode.REPLAY_TRUNCATED
                            and waited >= server.get_inject_after_seconds()):
                        self._frame(SignalStreamEventName.CONTROL,
                                    server.build_control(inject, head))
                        return
                    self._frame(SignalStreamEventName.HEARTBEAT,
                                {'stream_epoch': MOCK_EPOCH, 'seq': head,
                                 'now_msc': int(datetime.now(timezone.utc).timestamp() * 1000)})

        return Handler
