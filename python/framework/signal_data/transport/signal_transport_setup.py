"""
FiniexTestingIDE - Signal Transport Setup
Builds a live session's signal transport from the source resolved at startup (#141, #468).

Mirrors `tick_sources/tick_source_setup.py`, which does the same job for the other external
input a live session takes: a session should say WHICH subsystems it runs, not how each one
is assembled. One deliberate difference from that precedent — the transport is returned
built but NOT started, because the session that owns `stop()` should own `start()` too.

The mode is not decided here. It was resolved once at startup and is followed: a profile
with no SIGNAL worker starts nothing, and a mock session that mounted its series starts
nothing either — opening a connection there would mix live envelopes into a replay whose
whole purpose is that nothing arrives from outside.
"""

from dataclasses import dataclass
from typing import Optional

from python.configuration.sentiment_config_manager import SentimentConfigManager
from python.framework.exceptions.signal_data_errors import SignalSourceUnresolvedError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.signal_data.transport.abstract_signal_transport import (
    AbstractSignalTransport,
)
from python.framework.signal_data.producer.signal_health_probe import SignalHealthProbe
from python.framework.signal_data.transport.signal_inbox import SignalInbox
from python.framework.signal_data.signal_observed_accumulator import SignalObservedAccumulator
from python.framework.signal_data.transport.signal_poll_source import SignalPollSource
from python.framework.signal_data.transport.signal_stream_source import SignalStreamSource
from python.framework.types.config_types.sentiment_config_types import (
    ActiveProducer,
    SentimentStreamConfig,
)
from python.framework.types.signal_data_types import (
    SignalLiveBoot,
    SignalSourceMode,
    SignalSourceResolution,
    SignalTransportKind,
)


@dataclass
class SignalTransportSetup:
    """
    The three objects a live session's signal transport consists of (#141 Part 2a, #468).

    Returned together because they are wired to each other and useless apart: the transport
    fills the inbox, the accumulator records what arrived, and the loop drains the inbox.
    Built but NOT started — the session that owns `stop()` owns `start()` too, which is the
    one deliberate difference from the tick source's setup, where the factory starts the
    thread and the session stops it.

    Lives here rather than in `framework/types/` (§6) because its three members are live
    collaborators rather than data, and because `signal_data_types` cannot import them: the
    inbox imports that module, so the annotation would need a string and the string would
    name something the file cannot resolve — which is exactly what the undefined-name gate
    caught. Same shape as `reporting/builders/run_unit.py`, which bundles a run's objects
    beside the code that builds them.

    Args:
        inbox: Hand-off buffer the loop drains once per pass
        transport: The live source, built and ready to start
        observed: Accumulator recording what the arriving envelopes state about themselves
    """
    inbox: SignalInbox
    transport: AbstractSignalTransport
    observed: SignalObservedAccumulator


def setup_signal_transport(
    symbol: str,
    signal_source: Optional[SignalSourceResolution],
    signal_boot: Optional[SignalLiveBoot],
    logger: ScenarioLogger,
) -> Optional[SignalTransportSetup]:
    """
    Build the live signal transport, when this session needs one.

    A configuration error here ABORTS the session (§35): a bot told to trade on live
    sentiment must not silently fall back to whatever the archive happened to hold.

    Args:
        symbol: The session's trading symbol, for the observed-series scope
        signal_source: What feeds this session's SIGNAL workers, resolved at startup
        signal_boot: What the boot bridge established — the cursor and the producer's
            served stream values; required for the push transport, unused by the poll path
        logger: Session logger — operator-relevant failures belong here (§35)

    Returns:
        The wired transport, or None when this session opens no connection at all
    """
    if signal_source is None:
        raise SignalSourceUnresolvedError(
            'The pipeline was built without resolving a signal source — the transport '
            'cannot decide on its own what this session reads.')
    if signal_source.mode is not SignalSourceMode.LIVE:
        return None

    sentiment_config = SentimentConfigManager().get_config()
    is_stream = signal_source.transport is SignalTransportKind.STREAM
    transport_config = sentiment_config.stream if is_stream else sentiment_config.poll
    pipeline_id = transport_config.pipeline_id
    if not pipeline_id:
        named = 'stream' if is_stream else 'poll'
        raise SignalSourceUnresolvedError(
            f'Signal transport is enabled but {named}.pipeline_id is empty — '
            f'name the producer pipeline in sentiment_config.json.')

    producer = SentimentConfigManager().resolve_active_producer()
    # Announce which endpoint and which file answered. With several registered endpoints
    # and a tracked empty credential default, "I thought I was on dev" and "the token is
    # configured" are otherwise both unverifiable from the log — and an empty token now
    # means 401 on every route except /v1/health.
    logger.info(f'📡 Producer endpoint ← {producer.describe()}')
    if not producer.credential.is_configured():
        logger.warning(
            '📡 No producer token configured — requests go out without an Authorization '
            'header. Every route except /v1/health will answer 401. Place the token in '
            f"user_configs/credentials/ for endpoint '{producer.name}'.")

    # The probe borrows the transport's address on purpose: the question it answers is
    # which journal these envelopes come from, so asking a second address could answer
    # about an engine that is not the one delivering.
    health_config = sentiment_config.health
    health_probe = (
        SignalHealthProbe(
            config=health_config,
            base_url=producer.base_url,
            logger=logger,
            api_token=producer.credential.token,
            pipeline_id=pipeline_id,
            source=sentiment_config.get_source(pipeline_id),
        )
        if health_config.enabled else None
    )

    observed = SignalObservedAccumulator(source=pipeline_id, symbol=symbol)
    inbox = SignalInbox()

    if is_stream:
        transport = _build_stream_source(
            stream_config=sentiment_config.stream, producer=producer,
            signal_source=signal_source, signal_boot=signal_boot, inbox=inbox,
            observed=observed, health_probe=health_probe, logger=logger)
    else:
        transport = SignalPollSource(
            config=sentiment_config.poll,
            producer=producer,
            signal_kind=signal_source.signal_kind,
            inbox=inbox,
            logger=logger,
            health_probe=health_probe,
            observed=observed,
        )
    return SignalTransportSetup(inbox=inbox, transport=transport, observed=observed)


def _build_stream_source(
    stream_config: SentimentStreamConfig,
    producer: ActiveProducer,
    signal_source: SignalSourceResolution,
    signal_boot: Optional[SignalLiveBoot],
    inbox: SignalInbox,
    observed: SignalObservedAccumulator,
    health_probe: Optional[SignalHealthProbe],
    logger: ScenarioLogger,
) -> SignalStreamSource:
    """
    Build the push transport from what the boot bridge already established (#468).

    Nothing is re-derived here. The cursor comes from the archive slice mounted at startup
    and the keep-alive interval from the producer's own registry, both read once — a second
    read could disagree with the series the workers are holding.

    Args:
        stream_config: The stream transport's own settings
        producer: Active endpoint with its resolved credential
        signal_source: The resolved live source
        signal_boot: What the boot bridge established
        inbox: Hand-off buffer the transport fills
        observed: Accumulator the transport feeds on enqueue
        health_probe: Producer-identity probe, or None when disabled
        logger: Session logger

    Returns:
        The transport, not yet started
    """
    if signal_boot is None or signal_boot.stream_settings is None:
        raise SignalSourceUnresolvedError(
            'The signal stream was selected but startup established no producer stream '
            'settings — the session cannot open a connection without the keep-alive '
            'interval its watchdog measures against.')

    cursor = signal_boot.mount.cursor
    resume = cursor.describe() if cursor else 'no cursor — connecting for the snapshot'
    logger.info(
        f'📡 Signal stream resume point: {resume} · keep-alive '
        f'{signal_boot.stream_settings.heartbeat_seconds:.0f}s · replay window '
        f'{signal_boot.stream_settings.replay_window_hours:.0f}h')
    return SignalStreamSource(
        config=stream_config,
        producer=producer,
        stream_settings=signal_boot.stream_settings,
        signal_kind=signal_source.signal_kind,
        inbox=inbox,
        logger=logger,
        cursor=cursor,
        health_probe=health_probe,
        observed=observed,
    )
