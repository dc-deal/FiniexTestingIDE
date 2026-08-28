"""
FiniexTestingIDE - Stream Probe (#468)

The operator's window onto a transport that otherwise only shows itself inside a running
session. What matters here is not that it renders — it is that it refuses to call a dead
producer healthy, because a probe that says "fine" about a socket which opened and then
delivered nothing is worse than no probe at all.

The transport itself is pinned in its own suite; this covers the guards in front of it and
the verdict it returns.
"""

from unittest.mock import MagicMock

import pytest

from python.framework.signal_data.producer import signal_stream_probe
from python.framework.signal_data.producer.signal_stream_probe import run_stream_probe
from python.framework.types.config_types.sentiment_config_types import (
    ActiveProducer,
    ResolvedCredential,
    SentimentStreamConfig,
)
from python.framework.types.signal_data_types import (
    ProducerPipelineInfo,
    ProducerPipelineRegistry,
    ProducerStreamSettings,
)

PIPELINE = 'crypto_sentiment'
PRODUCER = ActiveProducer(
    name='test', base_url='http://producer.test',
    credential=ResolvedCredential(token='t', source='tests (in-memory)'))


def registry(ok: bool = True, served: bool = True, known: bool = True,
             detail: str = '') -> ProducerPipelineRegistry:
    """Build a registry read in whichever of the four shapes the test needs."""
    if not ok:
        return ProducerPipelineRegistry(ok=False, detail=detail or 'unreachable')
    return ProducerPipelineRegistry(
        ok=True,
        detail='1 pipeline(s)',
        stream=(ProducerStreamSettings(heartbeat_seconds=20.0, replay_window_hours=24.0)
                if served else None),
        pipelines=({PIPELINE: ProducerPipelineInfo(pipeline_id=PIPELINE,
                                                   cadence_seconds=600.0)}
                   if known else {}))


@pytest.fixture
def answering(monkeypatch):
    """Script the registry read, and keep the probe from opening a real connection."""
    def install(result: ProducerPipelineRegistry):
        monkeypatch.setattr(
            signal_stream_probe, 'fetch_pipeline_registry',
            lambda *args, **kwargs: result)
    return install


def probe(seconds: float = 0.0):
    """Run the probe against the scripted registry."""
    return run_stream_probe(
        producer=PRODUCER,
        stream_config=SentimentStreamConfig(enabled=True, pipeline_id=PIPELINE),
        logger=MagicMock(),
        seconds=seconds)


class TestTheGuardsInFront:
    """Each refusal names what a session would have hit, before a socket is opened."""

    def test_an_unreadable_registry_stops_before_connecting(self, answering):
        answering(registry(ok=False, detail='unreachable: TimeoutError'))
        result = probe()
        assert result.ok is False
        assert 'unreachable' in result.detail
        assert result.connections == 0

    def test_a_producer_that_does_not_serve_the_stream_values_stops(self, answering):
        """
        The same refusal a session makes, for the same reason: without the keep-alive
        interval there is nothing for the watchdog to measure against, and guessing one is
        a watchdog that fires on a healthy feed.
        """
        answering(registry(served=False))
        result = probe()
        assert result.ok is False
        assert 'heartbeat_seconds' in result.detail

    def test_an_unregistered_pipeline_stops_and_names_what_exists(self, answering):
        answering(registry(known=False))
        result = probe()
        assert result.ok is False
        assert PIPELINE in result.detail
        assert 'Known' in result.detail


class TestTheVerdict:
    """A probe that calls a silent producer healthy is worse than no probe."""

    def test_a_socket_that_opened_and_delivered_nothing_is_not_a_success(self, answering):
        """
        'connecting' means exactly that — the connection was attempted and no frame ever
        came back. Counting it as ok is the failure mode a probe exists to expose, and the
        CLI exits non-zero on this, so it must not read as healthy.
        """
        answering(registry())
        result = probe(seconds=0.0)
        assert result.state in ('connecting', 'error')
        assert result.ok is False

    def test_the_probe_claims_no_cursor_of_its_own(self, answering):
        """
        A probe that advanced a session's cursor would consume envelopes the session it was
        meant to diagnose still needs.
        """
        answering(registry())
        assert probe(seconds=0.0).cursor == ''
