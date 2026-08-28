"""
FiniexTestingIDE - Producer Pipeline Registry Reader (#468)

Three numbers live on `GET /v1/pipelines` that we deliberately do not configure: the
evaluation cadence, the keep-alive interval and the replay window. Each was a candidate
for a constant on our side, and a local copy of somebody else's number reports a feed
outage that never happened on the day they change it.

What is pinned here is the reading, not the route. In particular that an absent value is
reported as absent: a guessed keep-alive interval is a watchdog that fires on a healthy
feed, which is worse than refusing to start.
"""

import pytest

from python.framework.signal_data.producer import signal_pipelines_reader
from python.framework.signal_data.producer.signal_pipelines_reader import (
    describe_registry,
    fetch_pipeline_registry,
)
from python.framework.types.config_types.sentiment_config_types import (
    ActiveProducer,
    ResolvedCredential,
)
from python.framework.types.signal_data_types import ProducerRead

PRODUCER = ActiveProducer(
    name='test', base_url='http://producer.test',
    credential=ResolvedCredential(token='t', source='tests (in-memory)'))

SERVED = {
    'stream': {'heartbeat_seconds': 20, 'replay_window_hours': 24},
    'pipelines': [
        {'pipeline_id': 'crypto_sentiment', 'cadence_seconds': 600,
         'outcome_type': 'sentiment_fear_greed', 'trigger_type': 'bar_close'},
        {'pipeline_id': 'forex_macro_sentiment', 'cadence_seconds': 600},
    ],
}


@pytest.fixture
def answering(monkeypatch):
    """Make the route answer with a given payload, or fail in a given way."""
    def install(payload=None, read=None):
        result = read if read is not None else ProducerRead(ok=True, payload=payload)
        monkeypatch.setattr(
            signal_pipelines_reader, 'fetch_json',
            lambda *args, **kwargs: result)
    return install


class TestReadingTheRegistry:
    """The shape the producer settled on: engine-wide in the response, per-stream on a row."""

    def test_the_pipelines_are_keyed_by_id(self, answering):
        answering(SERVED)
        registry = fetch_pipeline_registry(PRODUCER)
        assert registry.ok
        assert set(registry.pipelines) == {'crypto_sentiment', 'forex_macro_sentiment'}
        assert registry.pipelines['crypto_sentiment'].cadence_seconds == 600

    def test_the_stream_values_are_read_from_response_level(self, answering):
        """
        Engine-wide, not per row. A per-row copy would claim to be a per-stream property,
        and someone eventually sets two of them differently.
        """
        answering(SERVED)
        stream = fetch_pipeline_registry(PRODUCER).stream
        assert stream.heartbeat_seconds == 20
        assert stream.replay_window_hours == 24

    def test_a_bare_list_of_pipelines_still_reads(self, answering):
        """The shape before the stream values joined it — rows, no envelope."""
        answering(SERVED['pipelines'])
        registry = fetch_pipeline_registry(PRODUCER)
        assert registry.ok
        assert len(registry.pipelines) == 2
        assert registry.stream is None

    def test_a_row_without_an_id_is_skipped_rather_than_keyed_by_nothing(self, answering):
        answering({'pipelines': [{'cadence_seconds': 600}, SERVED['pipelines'][0]]})
        assert set(fetch_pipeline_registry(PRODUCER).pipelines) == {'crypto_sentiment'}


class TestWhatIsNotServed:
    """An absent value is reported as absent — never defaulted into a plausible number."""

    @pytest.mark.parametrize('block', [
        {},
        {'heartbeat_seconds': 20},
        {'replay_window_hours': 24},
        {'heartbeat_seconds': 'soon', 'replay_window_hours': 24},
    ])
    def test_a_partial_stream_block_yields_no_settings(self, answering, block):
        answering({'stream': block, 'pipelines': SERVED['pipelines']})
        registry = fetch_pipeline_registry(PRODUCER)
        assert registry.ok, 'the pipelines still read — only the stream values are missing'
        assert registry.stream is None

    def test_a_missing_cadence_is_none_and_not_zero(self, answering):
        """
        Zero would divide, and a staleness threshold computed from it would be instant.
        """
        answering({'pipelines': [{'pipeline_id': 'crypto_sentiment'}]})
        assert fetch_pipeline_registry(PRODUCER).pipelines[
            'crypto_sentiment'].cadence_seconds is None

    def test_a_boolean_is_not_a_number(self, answering):
        answering({'pipelines': [{'pipeline_id': 'x', 'cadence_seconds': True}]})
        assert fetch_pipeline_registry(PRODUCER).pipelines['x'].cadence_seconds is None


class TestWhenTheRouteDoesNotAnswer:
    """A refused credential and an unreachable address stay separable to the operator."""

    def test_a_rejected_credential_is_carried_through(self, answering):
        answering(read=ProducerRead(
            ok=False, detail='401 — the producer refused the credential.',
            credential_rejected=True, status_code=401))
        registry = fetch_pipeline_registry(PRODUCER)
        assert registry.ok is False
        assert registry.credential_rejected is True

    def test_an_unreadable_shape_is_reported_rather_than_guessed(self, answering):
        answering('a string, somehow')
        registry = fetch_pipeline_registry(PRODUCER)
        assert registry.ok is False
        assert 'shape' in registry.detail


class TestTheOperatorLine:
    """One line, and it must say when the stream values are not there yet."""

    def test_it_names_the_pipelines_and_the_served_values(self, answering):
        answering(SERVED)
        line = describe_registry(fetch_pipeline_registry(PRODUCER))
        assert 'crypto_sentiment (600s)' in line
        assert 'keep-alive 20s' in line

    def test_it_says_when_the_stream_values_are_not_served(self, answering):
        answering({'pipelines': SERVED['pipelines']})
        assert 'not served' in describe_registry(fetch_pipeline_registry(PRODUCER))

    def test_a_failed_read_describes_itself(self, answering):
        answering(read=ProducerRead(ok=False, detail='unreachable: TimeoutError'))
        assert 'unreachable' in describe_registry(fetch_pipeline_registry(PRODUCER))
