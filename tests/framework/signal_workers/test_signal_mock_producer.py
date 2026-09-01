"""
FiniexTestingIDE - Signal Mock Producer
The local stand-in that makes the transport's control codes visible (#468).

Why it exists at all is the finding worth keeping: every mock run in this project mounts
its signal series from the archive, which is what makes a replay reproducible — and
therefore opens no connection. So the whole transport, and above all the four control codes
a healthy producer will never emit on request, is unreachable from a mock session by
construction, not by omission.

These tests run the REAL transport against the stand-in over a real socket. Nothing is
patched: if the stand-in stopped speaking the producer's frame grammar, the production
reader would refuse it here first.
"""

import pytest

from python.framework.signal_data.producer.signal_mock_producer import (
    MOCK_FIRST_SEQ,
    MOCK_SNAPSHOT_COUNT,
    SignalMockProducer,
)
from python.framework.signal_data.producer.signal_stream_probe import run_stream_probe
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.config_types.sentiment_config_types import (
    ActiveProducer,
    ResolvedCredential,
    SentimentStreamConfig,
)
from python.framework.types.signal_data_types import SignalStreamControlCode

# The stand-in's human-facing pacing is deliberately slow; a test shortens both ends. The
# window must outlast injection PLUS the reconnect backoff, or a followed rewind looks
# abandoned — which is what it did on the first run of this file.
PROBE_SECONDS = 4.0
INJECT_AFTER = 1.0
RECONNECT_BACKOFF_S = 0.5


def probe(mock_logger, inject=None):
    """
    Run one probe against a freshly started stand-in.

    Args:
        mock_logger: Logger the transport reports through
        inject: Control code to emit once live, or None

    Returns:
        What the probe established
    """
    mock = SignalMockProducer(inject=inject, inject_after_seconds=INJECT_AFTER).start()
    try:
        return run_stream_probe(
            producer=ActiveProducer(
                name='mock', base_url=mock.get_base_url(),
                credential=ResolvedCredential(token='t', source='(built in)')),
            stream_config=SentimentStreamConfig(
                enabled=True, pipeline_id=mock.get_pipeline_id(),
                connection=ConnectionPolicy(
                    initial_delay_s=RECONNECT_BACKOFF_S,
                    max_delay_s=RECONNECT_BACKOFF_S)),
            logger=mock_logger,
            seconds=PROBE_SECONDS)
    finally:
        mock.stop()


class TestTheStandInSpeaksTheProducersContract:
    """If it did not, the production reader would be the first thing to say so."""

    def test_a_healthy_stand_in_goes_live_and_delivers_its_snapshot(self, mock_logger):
        result = probe(mock_logger)

        assert result.ok
        assert result.state == 'live'
        assert len(result.arrivals) == MOCK_SNAPSHOT_COUNT
        assert result.arrivals[0].seq == MOCK_FIRST_SEQ
        assert result.connections == 1, 'a healthy stream must not reconnect'

    def test_the_registry_carries_both_served_stream_values(self, mock_logger):
        """Without them a session refuses to start, so the stand-in must serve them."""
        result = probe(mock_logger)

        assert 'keep-alive' in result.detail
        assert 'replay window' in result.detail


class TestTheControlCodesReachTheOperator:
    """
    One test per code, because their responses are deliberately different.

    This is the surface a mock AutoTrader session cannot produce: it never connects.
    """

    def test_epoch_changed_reconnects_through_the_connect_path(self, mock_logger):
        result = probe(mock_logger, SignalStreamControlCode.EPOCH_CHANGED)

        assert result.connections > 1, 'a rewind must be followed, not abandoned'
        assert 'epoch 2' in result.cursor, (
            'the new epoch must be carried into the cursor, or the reconnect asks the '
            'producer for a position in a series that no longer exists')

    def test_cursor_ahead_stops_and_never_resumes(self, mock_logger):
        """Our store was restored — silently resuming would paper over exactly that."""
        result = probe(mock_logger, SignalStreamControlCode.CURSOR_AHEAD)

        assert result.state == 'cursor_ahead'
        assert not result.ok
        assert result.connections == 1, 'terminal means terminal — no reconnect'

    def test_auth_revoked_stops_as_a_credential_condition(self, mock_logger):
        result = probe(mock_logger, SignalStreamControlCode.AUTH_REVOKED)

        assert result.state == 'unauthorized'
        assert result.transport_errors == 0, (
            'a revoked token is not a transport fault — counting it as one sends the '
            'operator to the wrong system')

    def test_replay_truncated_continues(self, mock_logger):
        """
        The one non-terminal diagnosis: the cursor is old, not unusable.

        Pinned beside the others because collapsing the family into "cursor problem →
        stop" would cost a recoverable session for a hole the staleness contract covers.
        """
        result = probe(mock_logger, SignalStreamControlCode.REPLAY_TRUNCATED)

        assert result.state == 'live'
        assert len(result.arrivals) == MOCK_SNAPSHOT_COUNT


@pytest.mark.parametrize('code', list(SignalStreamControlCode))
def test_every_control_code_can_be_injected(code, mock_logger):
    """
    A code the stand-in cannot emit is a code nobody will ever look at.

    Parametrized over the enum on purpose: a sixth code added to the vocabulary fails here
    until the stand-in can produce it.
    """
    result = probe(mock_logger, code)

    assert result.tape, f'{code.value} produced no transport tape at all'
