"""
FiniexTestingIDE - Signal Stream Probe
Opens the producer's stream briefly and reports what arrived (#468).

The operator's window onto a transport that otherwise only shows itself inside a running
session. It connects exactly as a session would — same source, same cursor rules, same
control-code routing — holds the connection for a few seconds, and prints the transport
tape plus what reached the inbox.

Deliberately cursor-less: a probe asks for the current snapshot and never claims a
position, because a probe that advanced a session's cursor would consume envelopes the
session it was meant to diagnose still needs.

Costs nothing. The registry read and the stream are both free routes; the one route in the
producer that spends is not registered in production at all.
"""

import time

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.signal_data.transport.signal_inbox import SignalInbox
from python.framework.signal_data.producer.signal_pipelines_reader import (
    describe_registry,
    fetch_pipeline_registry,
)
from python.framework.signal_data.transport.signal_stream_source import SignalStreamSource
from python.framework.types.config_types.sentiment_config_types import (
    ActiveProducer,
    SentimentStreamConfig,
)
from python.framework.types.signal_data_types import StreamProbeResult

# How long the probe holds the connection when the caller names no duration. Long enough
# to see the connect, the replay boundary and at least one keep-alive at their 20 s beat.
DEFAULT_PROBE_SECONDS = 25.0


def run_stream_probe(
    producer: ActiveProducer,
    stream_config: SentimentStreamConfig,
    logger: ScenarioLogger,
    seconds: float = DEFAULT_PROBE_SECONDS,
    signal_kind: str = 'llm_sentiment',
) -> StreamProbeResult:
    """
    Hold the stream open for a while and report what it did.

    Args:
        producer: Active endpoint with its resolved credential
        stream_config: The stream transport's own settings
        logger: Logger the transport reports through
        seconds: How long to hold the connection
        signal_kind: Payload kind the arrivals are filed under

    Returns:
        What arrived, what the transport recorded, and why it stopped if it did
    """
    registry = fetch_pipeline_registry(producer)
    if not registry.ok:
        return StreamProbeResult(
            ok=False, detail=f'the pipeline registry could not be read: {registry.detail}')
    if registry.stream is None:
        return StreamProbeResult(
            ok=False,
            detail='this producer does not serve heartbeat_seconds and '
                   'replay_window_hours on /v1/pipelines yet, so a session could not open '
                   'a stream either — the watchdog has no interval to measure against')
    if stream_config.pipeline_id not in registry.pipelines:
        known = ', '.join(sorted(registry.pipelines)) or '(none registered)'
        return StreamProbeResult(
            ok=False,
            detail=f"stream.pipeline_id '{stream_config.pipeline_id}' is not registered "
                   f'with this producer. Known: {known}')

    inbox = SignalInbox()
    source = SignalStreamSource(
        config=stream_config,
        producer=producer,
        stream_settings=registry.stream,
        signal_kind=signal_kind,
        inbox=inbox,
        logger=logger,
    )
    source.start()
    try:
        time.sleep(seconds)
    finally:
        source.stop()

    stats = source.get_transport_stats()
    arrivals = inbox.drain().get(signal_kind, [])
    # 'connecting' is deliberately NOT a success: it means the socket opened and nothing
    # ever came back, which is the exact case a probe exists to expose.
    return StreamProbeResult(
        ok=stats.state in ('live', 'replay'),
        detail=describe_registry(registry),
        state=stats.state,
        seconds=seconds,
        connections=source.get_stats()[0],
        arrivals=arrivals,
        cursor=source.get_cursor().describe() if source.get_cursor() else '',
        tape=[f'{event.at:%H:%M:%S} · {event.message}' for event in stats.tape],
        contract_errors=stats.contract_errors,
        transport_errors=stats.transport_errors)


def print_stream_probe(result: StreamProbeResult) -> None:
    """
    Render one stream probe for the operator.

    Args:
        result: What the probe established
    """
    print()
    print('=' * 72)
    print('📡 PRODUCER STREAM PROBE')
    print('=' * 72)
    if not result.ok and not result.tape:
        print(f'❌ {result.detail}')
        print('=' * 72)
        return

    print(f'Registry:   {result.detail}')
    print(f'State:      {result.state} after {result.seconds:.0f}s, '
          f'{result.connections} connection(s)')
    print(f'Cursor:     {result.cursor or "none — the probe claims no position"}')
    print(f'Arrivals:   {len(result.arrivals)} envelope(s)')
    for snapshot in result.arrivals:
        print(f'   seq {snapshot.seq} · epoch {snapshot.stream_epoch} · '
              f'{snapshot.trigger_reason or "unknown-trigger"} pass')
    if result.contract_errors or result.transport_errors:
        print(f'Errors:     {result.contract_errors} contract · '
              f'{result.transport_errors} transport')
    print('Tape:')
    for line in result.tape:
        print(f'   {line}')
    print('=' * 72)

