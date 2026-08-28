"""
FiniexTestingIDE - Signal Pipelines Reader
Reads the producer's pipeline registry: `GET /v1/pipelines` (#468).

Three facts live here that we deliberately do NOT configure: the evaluation cadence, the
keep-alive interval and the replay window. Each was a candidate for a constant in our own
configuration, and each is served instead — a local copy of somebody else's number is a
second answer to a question they already answer, so the day they change it we report a
feed outage that never happened.

The route is token-gated and spends nothing. Their engine has exactly one route that turns
a request into money, `POST /v1/pipelines/{id}/run`, and it is not registered in
production — absent from the schema rather than one configuration edit away from live.
That is why a release certificate can prove the feed contract without buying an LLM call.
"""

from typing import Any, List, Optional

from python.framework.signal_data.producer.signal_http_reader import fetch_json
from python.framework.types.config_types.sentiment_config_types import ActiveProducer
from python.framework.types.signal_data_types import (
    ProducerPipelineInfo,
    ProducerPipelineRegistry,
    ProducerStreamSettings,
)

PIPELINES_ROUTE = '/v1/pipelines'


def fetch_pipeline_registry(
    producer: ActiveProducer,
    timeout_s: float = 10.0,
) -> ProducerPipelineRegistry:
    """
    Read the producer's registered pipelines and its engine-wide stream values.

    Args:
        producer: Active endpoint with its resolved credential
        timeout_s: Request timeout

    Returns:
        The registry, or the classified reason nothing came back
    """
    url = f"{producer.base_url.rstrip('/')}{PIPELINES_ROUTE}"
    read = fetch_json(url, producer.credential.token, timeout_s)
    if not read.ok:
        return ProducerPipelineRegistry(
            ok=False, detail=read.detail,
            credential_rejected=read.credential_rejected)

    payload = read.payload
    rows = _rows(payload)
    if rows is None:
        return ProducerPipelineRegistry(
            ok=False,
            detail=f'{PIPELINES_ROUTE} answered a shape this reader does not know: '
                   f'{type(payload).__name__}')

    pipelines = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pipeline_id = str(row.get('pipeline_id') or '')
        if not pipeline_id:
            continue
        pipelines[pipeline_id] = ProducerPipelineInfo(
            pipeline_id=pipeline_id,
            cadence_seconds=_optional_number(row.get('cadence_seconds')))

    return ProducerPipelineRegistry(
        ok=True,
        detail=f'{len(pipelines)} pipeline(s)',
        stream=_stream_settings(payload),
        pipelines=pipelines)


def _rows(payload: Any) -> Optional[List[Any]]:
    """
    The pipeline rows, whichever of the two documented shapes answered.

    Args:
        payload: The decoded response

    Returns:
        The rows, or None when the response is neither shape
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        rows = payload.get('pipelines')
        return rows if isinstance(rows, list) else []
    return None


def _stream_settings(payload: Any) -> Optional[ProducerStreamSettings]:
    """
    The engine-wide stream values, when the producer serves them.

    At RESPONSE level rather than on a pipeline row, and the distinction matters: they are
    properties of the engine, so a per-row copy would claim to be a per-stream property
    and someone eventually sets two of them differently. Absent means the producer has not
    shipped them yet — reported as absent, never guessed at, because a guessed keep-alive
    interval is a watchdog that fires on a healthy feed.

    Args:
        payload: The decoded response

    Returns:
        The settings, or None when the response does not carry both
    """
    if not isinstance(payload, dict):
        return None
    block = payload.get('stream')
    if not isinstance(block, dict):
        return None
    heartbeat = _optional_number(block.get('heartbeat_seconds'))
    window = _optional_number(block.get('replay_window_hours'))
    if heartbeat is None or window is None:
        return None
    return ProducerStreamSettings(
        heartbeat_seconds=heartbeat, replay_window_hours=window)


def _optional_number(value: Any) -> Optional[float]:
    """
    Read a numeric field, tolerating absence but never a non-number.

    Args:
        value: The raw field

    Returns:
        The value as a float, or None when it is absent or not a number
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def describe_registry(registry: ProducerPipelineRegistry) -> str:
    """
    One operator line summarizing a registry read.

    Args:
        registry: The read to describe

    Returns:
        The summary line
    """
    if not registry.ok:
        return registry.detail
    named = ', '.join(
        f'{info.pipeline_id} ({info.cadence_seconds:.0f}s)'
        if info.cadence_seconds else info.pipeline_id
        for info in registry.pipelines.values()) or 'none registered'
    if registry.stream is None:
        return f'{named} · stream values not served'
    return (f'{named} · keep-alive {registry.stream.heartbeat_seconds:.0f}s · '
            f'replay window {registry.stream.replay_window_hours:.0f}h')
