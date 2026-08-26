"""
Signal source resolution — which source feeds a session's SIGNAL workers (#141).

The resolver replaced three separate derivations of the same question, two of which
disagreed with the one that did the provider wiring. The regression class at the bottom
pins the two concrete failures that motivated it; both of them abort or misbehave on the
pre-resolver code.
"""

from unittest.mock import MagicMock

import pytest

from python.framework.exceptions.signal_data_errors import SignalSourceUnresolvedError
from python.framework.signal_data.signal_source_resolver import SignalSourceResolver
from python.framework.types.config_types.sentiment_config_types import SentimentConfig
from python.framework.types.process_data_types import ProcessDataPackage
from python.framework.types.signal_data_types import SignalSourceMode, SignalTransportKind
from python.framework.workers.abstract_signal_worker import AbstractSignalWorker
from python.framework.workers.abstract_worker import AbstractWorker


def signal_worker(name: str = 'sentiment', kind: str = 'llm_sentiment') -> MagicMock:
    """
    A stand-in that satisfies the two things the resolver asks of a SIGNAL worker.

    Args:
        name: Instance name, used in the error message
        kind: The signal kind the worker consumes

    Returns:
        A spec'd mock that passes isinstance(AbstractSignalWorker)
    """
    worker = MagicMock(spec=AbstractSignalWorker)
    worker.name = name
    worker.get_consumed_signal_kind.return_value = kind
    return worker


def plain_worker(name: str = 'rsi') -> MagicMock:
    """
    An indicator-style worker: everything a worker is, minus the signal contract.

    Args:
        name: Instance name

    Returns:
        A spec'd mock that does NOT pass isinstance(AbstractSignalWorker)
    """
    worker = MagicMock(spec=AbstractWorker)
    worker.name = name
    return worker


def mounted_package() -> MagicMock:
    """A stand-in for a prepared scenario package — its presence is what the resolver reads."""
    return MagicMock(spec=ProcessDataPackage)


def config(poll: bool = False, stream: bool = False) -> SentimentConfig:
    """
    A sentiment config with the transports switched as asked.

    Args:
        poll: Enable the interim pull transport
        stream: Enable the push transport (#468, not built)

    Returns:
        A SentimentConfig carrying defaults everywhere else
    """
    return SentimentConfig(
        poll={'enabled': poll, 'pipeline_id': 'crypto_sentiment' if poll else ''},
        stream={'enabled': stream, 'pipeline_id': 'crypto_sentiment' if stream else ''})


class TestNoSignalWorker:
    """
    First question of the tree: a profile that reads no signals needs no source.

    The transport switch lives in sentiment_config.json and is therefore
    installation-wide, while the workers that consume it are declared per profile. A
    profile without one was never told to trade on signals, so the switch must not reach it.
    """

    def test_no_signal_worker_resolves_to_none(self):
        resolution = SignalSourceResolver.resolve(
            workers=[plain_worker(), plain_worker('bollinger')],
            package=None,
            sentiment_config=config(poll=True))

        assert resolution.mode is SignalSourceMode.NONE
        assert resolution.worker_count == 0
        assert resolution.transport is None

    def test_no_worker_at_all_resolves_to_none(self):
        resolution = SignalSourceResolver.resolve(
            workers=[], package=None, sentiment_config=config(poll=True))
        assert resolution.mode is SignalSourceMode.NONE


class TestMountedSource:
    """
    Second question: a prepared package is the source, and it outranks any transport.

    The PRESENCE of the package decides, never its contents — a replay is reproducible
    only because nothing arrives from outside, so an empty series must surface as a wiring
    problem rather than quietly become a live connection.
    """

    def test_package_resolves_to_mounted_even_with_poll_enabled(self):
        resolution = SignalSourceResolver.resolve(
            workers=[signal_worker(), plain_worker()],
            package=mounted_package(),
            sentiment_config=config(poll=True))

        assert resolution.mode is SignalSourceMode.MOUNTED
        assert resolution.worker_count == 1
        assert resolution.transport is None

    def test_package_resolves_to_mounted_with_no_transport_configured(self):
        resolution = SignalSourceResolver.resolve(
            workers=[signal_worker()],
            package=mounted_package(),
            sentiment_config=config())
        assert resolution.mode is SignalSourceMode.MOUNTED


class TestLiveSource:
    """Third question: no package and a SIGNAL worker means a transport must fill it."""

    def test_poll_enabled_resolves_to_live_poll(self):
        resolution = SignalSourceResolver.resolve(
            workers=[signal_worker()], package=None, sentiment_config=config(poll=True))

        assert resolution.mode is SignalSourceMode.LIVE
        assert resolution.transport is SignalTransportKind.POLL
        assert resolution.signal_kind == 'llm_sentiment'

    def test_no_transport_enabled_is_an_error(self):
        """The genuinely dangerous case: a worker that will never receive anything."""
        with pytest.raises(SignalSourceUnresolvedError, match='no source'):
            SignalSourceResolver.resolve(
                workers=[signal_worker()], package=None, sentiment_config=config())

    def test_two_signal_kinds_against_one_transport_is_an_error(self):
        with pytest.raises(SignalSourceUnresolvedError, match='#258'):
            SignalSourceResolver.resolve(
                workers=[signal_worker('crypto', 'llm_sentiment'),
                         signal_worker('macro', 'forex_macro_sentiment')],
                package=None,
                sentiment_config=config(poll=True))

    def test_stream_is_answered_rather_than_silently_ignored(self):
        """
        `stream.enabled` has no transport behind it yet (#468).

        Treating it as "not poll, so nothing" would leave the workers empty with nothing
        to fill them — a session that decides on BLIND forever and never says why.
        """
        with pytest.raises(SignalSourceUnresolvedError, match='#468'):
            SignalSourceResolver.resolve(
                workers=[signal_worker()], package=None, sentiment_config=config(stream=True))


class TestRegressionsThatMotivatedTheResolver:
    """
    The two concrete failures, pinned.

    Both were invisible to the suite: pytest sets FINIEX_CONFIG_ISOLATION, so the
    workspace override that enables the transport was never seen, and the CLI path — the
    only one that reads it — is the one that broke.
    """

    def test_a_non_signal_profile_is_not_aborted_by_an_installation_wide_switch(self):
        """
        Was: every profile without a SIGNAL worker failed startup while poll was enabled —
        20 of 24 profiles, including four live trading profiles, both field-study release
        gates, and mock tests with no broker connection at all.
        """
        resolution = SignalSourceResolver.resolve(
            workers=[plain_worker('rsi'), plain_worker('bollinger'), plain_worker('obv')],
            package=None,
            sentiment_config=config(poll=True))

        assert resolution.mode is SignalSourceMode.NONE
        assert resolution.transport is None, (
            'A profile that reads no signals must not cause a transport to be started')

    def test_a_mock_session_does_not_reach_for_the_live_producer(self):
        """
        Was: a mock session with a SIGNAL worker mounted its archive series AND opened a
        live poll against the production producer, folding live envelopes into a replay
        whose whole purpose is determinism.
        """
        resolution = SignalSourceResolver.resolve(
            workers=[signal_worker()],
            package=mounted_package(),
            sentiment_config=config(poll=True))

        assert resolution.mode is SignalSourceMode.MOUNTED
        assert resolution.transport is None, (
            'A mounted session must not open a live transport — that is what makes a '
            'replay reproducible')
