"""
FiniexTestingIDE - Signal Boot Resolver
What a live session knows about its signal source before it opens a connection (#468, #473).

One layer above SignalBootBridge: the bridge MOUNTS an archive slice, this decides what to
mount and what to do when the producer cannot be reached at all. It lives in the signal
package rather than in the AutoTrader startup because the question it answers — "what does
this session know, and how blind is it" — belongs to the signal domain; the startup flow
only asks it once and passes the answer on.
"""

from datetime import datetime, timezone

from python.configuration.sentiment_config_manager import SentimentConfigManager
from python.framework.exceptions.signal_data_errors import SignalSourceUnresolvedError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.signal_data.producer.signal_pipelines_reader import read_registry_or_raise
from python.framework.signal_data.transport.signal_boot_bridge import SignalBootBridge
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.signal_data_types import (
    SignalBootMount,
    SignalLiveBoot,
    SignalSeries,
    SignalSourceResolution,
    SignalTransportKind,
)
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder

# How far back the archive is read when the producer could not be asked for its own replay
# window at boot (#473, degraded start). Wide enough that a session restarted after a night
# still mounts something; the staleness contract judges whether it is usable, which is not
# this number's job.
_DEGRADED_REPLAY_WINDOW_HOURS: float = 24.0


def prepare_live_signal_boot(
    config: AutoTraderConfig,
    signal_source: SignalSourceResolution,
    logger: ScenarioLogger,
) -> SignalLiveBoot:
    """
    Establish what a live session knows before it opens a connection (#468).

    Two things happen here and both happen exactly once. The producer's registry is read,
    which is where the keep-alive interval and the replay window come from — served rather
    than configured, so a change on their side cannot reach us as a phantom outage. And the
    archive slice is mounted, which is what turns a restart's opening state from BLIND into
    STALE.

    A configuration problem ABORTS the session (§35): a bot told to trade on live sentiment
    must not quietly proceed on whatever happened to be on disk. An EMPTY archive does not —
    starting blind is a legitimate state that the staleness contract already describes.

    Args:
        config: The session's profile
        signal_source: The resolved live source, naming the transport
        logger: Session logger

    Returns:
        The mounted slice, its cursor, and the served stream values where they apply
    """
    sentiment = SentimentConfigManager().get_config()
    empty = SignalBootMount(
        series=SignalSeries(signal_kind=signal_source.signal_kind, snapshots=[]),
        reason='live poll transport — no boot mount')

    if signal_source.transport is not SignalTransportKind.STREAM:
        return SignalLiveBoot(mount=empty)

    pipeline_id = sentiment.stream.pipeline_id
    if not pipeline_id:
        raise SignalSourceUnresolvedError(
            'The signal stream is enabled but stream.pipeline_id is empty — name the '
            'producer pipeline in sentiment_config.json.')

    producer = SentimentConfigManager().resolve_active_producer()
    # #473 — this read used to have ONE attempt and end the session on failure. A restart
    # at 03:14 while a reverse proxy cycles is exactly the moment nobody is watching, and
    # it is the moment the unattended month exists to survive.
    registry = run_with_ladder(
        lambda: read_registry_or_raise(producer),
        ConnectionLadder(
            name='signal_registry',
            policy=sentiment.stream.boot_connection,
            logger=logger,
        ),
    )
    if registry is None:
        # The configured rule is degrade: start on the archive slice alone. The state is
        # STALE, not blind — the boot bridge mounts what the archive has and the #434
        # staleness contract carries it from the first tick, out loud.
        logger.error(
            '📡 Starting DEGRADED without the producer registry: the stream cannot open '
            'without the keep-alive interval its watchdog measures against. The mounted '
            'archive slice carries the session and the staleness contract will declare it.'
        )
        return SignalLiveBoot(mount=_mount_archive_only(
            config, signal_source, pipeline_id, logger))
    if pipeline_id not in registry.pipelines:
        known = ', '.join(sorted(registry.pipelines)) or '(none registered)'
        raise SignalSourceUnresolvedError(
            f"stream.pipeline_id '{pipeline_id}' is not registered with producer "
            f'{producer.name}. Known pipelines: {known}.')
    if registry.stream is None:
        raise SignalSourceUnresolvedError(
            f'Producer {producer.name} does not serve heartbeat_seconds and '
            f'replay_window_hours on /v1/pipelines. The stream reads both rather than '
            f'configuring them, and guessing a keep-alive interval is a watchdog that '
            f'fires on a healthy feed.')

    settings = registry.stream
    # The one wall-clock observation this makes, and it is a SETUP question — how far back
    # to read the archive — never an event stamp (§9).
    now = datetime.now(timezone.utc)
    mount = SignalBootBridge.mount(
        pipeline_id=pipeline_id,
        symbol=config.symbol,
        signal_kind=signal_source.signal_kind,
        replay_window_hours=settings.replay_window_hours,
        now=now,
        logger=logger)

    logger.info(f'📡 Signal boot bridge: {mount.reason}')
    if mount.beyond_replay_window:
        logger.warning(
            f"📡 The mounted cursor is older than the producer's replay window "
            f'({settings.replay_window_hours:.0f}h), so the connect replay will be '
            f'truncated and the gap between archive and stream stays unfilled. The '
            f'staleness contract covers it; a fresher signal import would close it.')
    return SignalLiveBoot(mount=mount, stream_settings=settings)


def _mount_archive_only(
    config: AutoTraderConfig,
    signal_source: SignalSourceResolution,
    pipeline_id: str,
    logger: ScenarioLogger,
) -> SignalBootMount:
    """
    Mount what the archive holds when the producer could not be reached at boot (#473).

    The replay window is the producer's number and we did not get it, so the mount uses
    the configured fallback span. That is a SETUP question — how far back to read — never
    an event stamp (§9).

    Args:
        config: AutoTrader configuration
        signal_source: The resolved live source
        pipeline_id: The producer pipeline this session consumes
        logger: Session logger

    Returns:
        The mounted slice, or an empty one when the archive has nothing either
    """
    return SignalBootBridge.mount(
        pipeline_id=pipeline_id,
        symbol=config.symbol,
        signal_kind=signal_source.signal_kind,
        replay_window_hours=_DEGRADED_REPLAY_WINDOW_HOURS,
        now=datetime.now(timezone.utc),
        logger=logger)
