"""
Producer identity probe (#141 Part 2a): which journal a live session consumed from.

Runs against a local stub, never against a real producer — a suite that needs someone else's
container to be running is a suite that fails for reasons unrelated to the code.

The point of the probe is that nothing on an envelope says which store it came from. Two
producer instances share a schema, a pipeline_id and a seq range, so a measurement taken
against a development instance is indistinguishable from one taken against the series a
release is certified on. What is pinned here is therefore mostly about what the probe REPORTS:
an identity recorded once, a change reported loudly, and a missing identity treated as a real
answer rather than as a probe that has not run yet.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import pytest

from python.framework.signal_data.signal_health_probe import SignalHealthProbe
from python.framework.types.config_types.sentiment_config_types import SentimentHealthConfig

DEV_JOURNAL = '9c3fa4c80d95'
PROD_JOURNAL = '138c68e48b15'


def health(journal_id=DEV_JOURNAL, environment='dev') -> dict:
    """A health document in the shape the producer serves."""
    return {
        'status': 'ok',
        'service': 'FiniexRAGEngine',
        'version': '0.3.2',
        'pass_timeout_seconds': 300,
        'journal_id': journal_id,
        'environment': environment,
    }


class _Stub:
    """A local HTTP stub serving a scripted sequence of health documents."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):                      # noqa: N802 — BaseHTTPRequestHandler API
                stub.requests.append({'path': self.path, **dict(self.headers)})
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


def build(stub, logger=None, token: str = ''):
    """A probe pointed at the stub, with its tape captured."""
    probe = SignalHealthProbe(
        config=SentimentHealthConfig(enabled=True, interval_s=0.05, request_timeout_s=3.0),
        base_url=stub.base_url,
        logger=logger or MagicMock(),
        api_token=token,
    )
    tape = []
    probe.set_event_sink(lambda message, level: tape.append((level.name, message)))
    return probe, tape


class TestIdentity:
    """What the producer says about itself, and what we keep of it."""

    def test_identity_is_recorded(self):
        with _Stub([health()]) as stub:
            probe, _ = build(stub)
            probe.probe_once()
        status = probe.get_status()
        assert status.journal_id == DEV_JOURNAL
        assert status.journal_name == 'dev'
        assert status.is_identified()

    def test_the_engine_facts_come_along(self):
        """The document also carries what we asked to be moved to engine level."""
        with _Stub([health()]) as stub:
            probe, _ = build(stub)
            probe.probe_once()
        status = probe.get_status()
        assert status.engine_version == '0.3.2'
        assert status.pass_timeout_s == 300
        assert status.probed_at is not None
        assert status.probed_at.tzinfo is not None

    def test_the_probe_asks_the_health_path(self):
        with _Stub([health()]) as stub:
            probe, _ = build(stub)
            probe.probe_once()
            assert stub.requests[0]['path'] == '/v1/health'

    def test_an_unnamed_journal_still_binds(self):
        """
        The name is looked up from a mapping on the producer's machine and may miss; the id
        is a fingerprint of its store and does not. A missed lookup is not a fault.
        """
        with _Stub([health(environment=None)]) as stub:
            probe, tape = build(stub)
            probe.probe_once()
        status = probe.get_status()
        assert status.journal_id == DEV_JOURNAL
        assert status.journal_name == 'unknown'
        assert status.is_identified()
        assert [level for level, _ in tape] == ['INFO']

    def test_the_identity_is_logged_not_only_shown(self):
        """
        The screen is ephemeral. A finished run must still be able to say which series its
        measurements belong to, so the identity goes to the session logger (§35).
        """
        logger = MagicMock()
        with _Stub([health()]) as stub:
            probe, _ = build(stub, logger=logger)
            probe.probe_once()
        logged = ' '.join(call.args[0] for call in logger.info.call_args_list)
        assert DEV_JOURNAL in logged and 'dev' in logged

    def test_get_status_hands_out_a_copy(self):
        """A caller mutating the snapshot must not rewrite what the probe knows."""
        with _Stub([health()]) as stub:
            probe, _ = build(stub)
            probe.probe_once()
            handed_out = probe.get_status()
            handed_out.journal_id = 'tampered'
            assert probe.get_status().journal_id == DEV_JOURNAL


class TestUnidentified:
    """A producer that names no journal is answering, not failing."""

    def test_null_journal_is_not_identified(self):
        """
        Two real cases: no store attached, or an identifier the producer's role may not
        read. Either way nothing certifies the session, which is a different state from
        'the probe has not run yet'.
        """
        with _Stub([health(journal_id=None)]) as stub:
            probe, tape = build(stub)
            probe.probe_once()
        status = probe.get_status()
        assert status.journal_id is None
        assert not status.is_identified()
        assert status.probed_at is not None
        assert [level for level, _ in tape] == ['NOTICE']

    def test_the_warning_is_not_repeated(self):
        """A cyclic probe against an unidentified producer must not fill the log."""
        logger = MagicMock()
        with _Stub([health(journal_id=None)]) as stub:
            probe, tape = build(stub, logger=logger)
            probe.probe_once()
            probe.probe_once()
            probe.probe_once()
        assert logger.warning.call_count == 1
        assert len(tape) == 1

    def test_an_identity_can_arrive_late(self):
        """A producer that attaches a store mid-session becomes identifiable."""
        with _Stub([health(journal_id=None), health()]) as stub:
            probe, _ = build(stub)
            probe.probe_once()
            probe.probe_once()
        assert probe.get_status().journal_id == DEV_JOURNAL
        assert not probe.get_status().journal_changed


class TestChange:
    """The case the cyclic cadence exists for."""

    def test_a_changed_journal_is_reported(self):
        """
        The cursor built so far — the last (stream_epoch, seq) accepted — belongs to the
        previous journal and means nothing in a different one. Silence here would let a
        session span two series without saying so.
        """
        logger = MagicMock()
        with _Stub([health(), health(journal_id=PROD_JOURNAL, environment='production')]) as stub:
            probe, tape = build(stub, logger=logger)
            probe.probe_once()
            probe.probe_once()
        status = probe.get_status()
        assert status.journal_id == PROD_JOURNAL
        assert status.journal_name == 'production'
        assert status.journal_changed
        assert ('ALERT', f'journal CHANGED {DEV_JOURNAL} → {PROD_JOURNAL}') in tape

    def test_a_change_reaches_the_error_pot(self):
        """Logged as an error so the session summary carries it, not only the screen (§35)."""
        logger = MagicMock()
        with _Stub([health(), health(journal_id=PROD_JOURNAL)]) as stub:
            probe, _ = build(stub, logger=logger)
            probe.probe_once()
            probe.probe_once()
        assert logger.error.call_count == 1
        assert PROD_JOURNAL in logger.error.call_args.args[0]

    def test_the_change_flag_is_sticky(self):
        """It describes the session, not the current answer — a later match does not undo it."""
        with _Stub([health(), health(journal_id=PROD_JOURNAL), health(journal_id=PROD_JOURNAL)]) as stub:
            probe, _ = build(stub)
            probe.probe_once()
            probe.probe_once()
            probe.probe_once()
        assert probe.get_status().journal_changed

    def test_losing_the_identity_is_also_a_change(self):
        """An identified producer that stops naming a journal is not a quiet downgrade."""
        logger = MagicMock()
        with _Stub([health(), health(journal_id=None)]) as stub:
            probe, _ = build(stub, logger=logger)
            probe.probe_once()
            probe.probe_once()
        assert probe.get_status().journal_changed
        assert logger.error.call_count == 1

    def test_an_unchanged_journal_stays_quiet(self):
        """Half-hourly probes over a multi-week run must not narrate themselves."""
        logger = MagicMock()
        with _Stub([health()]) as stub:
            probe, tape = build(stub, logger=logger)
            probe.probe_once()
            probe.probe_once()
            probe.probe_once()
        assert logger.info.call_count == 1
        assert logger.error.call_count == 0
        assert len(tape) == 1


class TestAuth:
    """The token is sent only when there is one."""

    def test_no_header_without_a_token(self):
        with _Stub([health()]) as stub:
            probe, _ = build(stub)
            probe.probe_once()
            assert 'Authorization' not in stub.requests[0]

    def test_bearer_header_with_a_token(self):
        with _Stub([health()]) as stub:
            probe, _ = build(stub, token='s3cret')
            probe.probe_once()
            assert stub.requests[0]['Authorization'] == 'Bearer s3cret'


class TestLifecycle:
    """Identity is never worth a crash."""

    def test_an_unreachable_producer_does_not_raise(self):
        probe = SignalHealthProbe(
            config=SentimentHealthConfig(interval_s=0.05, request_timeout_s=1.0),
            base_url='http://127.0.0.1:9',
            logger=MagicMock(),
        )
        probe.start()
        probe.stop()
        assert not probe.get_status().is_identified()

    def test_a_failed_probe_does_not_erase_what_is_known(self):
        """A network blip must not turn an identified session into an unidentified one."""
        with _Stub([health()]) as stub:
            probe, _ = build(stub)
            probe.probe_once()
        # The stub is gone now — the next probe can only fail.
        with pytest.raises(Exception):
            probe.probe_once()
        assert probe.get_status().journal_id == DEV_JOURNAL

    def test_stop_is_idempotent(self):
        with _Stub([health()]) as stub:
            probe, _ = build(stub)
            probe.start()
            probe.stop()
            probe.stop()
