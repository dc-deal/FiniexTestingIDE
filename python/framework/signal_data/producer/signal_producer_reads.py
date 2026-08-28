"""
FiniexTestingIDE - Producer Identity and Build Reads
The two token-free reads every certificate run makes, wherever its envelopes come from.

Shared rather than copied because they answer a question about the PRODUCER, not about the
transport: which journal is being written into, and which build is running. A poll
observation and a stream observation must not be able to disagree about that — two copies
of "what a health document means" is two places for the reading to drift, and this project
has now paid for a second derivation of an agreed contract three times.

Both routes are open by the producer's contract, so neither sends a token. `/v1/build` sits
behind a switch on their side, which is why its absence is recorded as "not offered" rather
than as a fault: a certificate that failed on their configuration choice would be asserting
something nobody promised.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from python.framework.signal_data.producer.signal_http_reader import fetch_json
from python.framework.types.signal_certificate_types import (
    FeedCheck,
    FeedProbeResult,
    ProducerBuild,
    ProducerIdentity,
    RouteCall,
)

HEALTH_ROUTE = '/v1/health'
BUILD_ROUTE = '/v1/build'

# What the producer calls a journal whose name its own mapping could not resolve.
UNRESOLVED_JOURNAL_NAME = 'unknown'


def read_identity(root: str, pipeline_id: str, timeout_s: float,
                  result: FeedProbeResult) -> None:
    """
    Ask the producer which journal it writes into, without sending a token.

    Args:
        root: Producer base URL, without a trailing slash
        pipeline_id: Source this run reads, to pick the right worker's cadence
        timeout_s: Request timeout
        result: Result being accumulated
    """
    result.routes_used.append(RouteCall('GET', HEALTH_ROUTE))
    read = fetch_json(f'{root}{HEALTH_ROUTE}', '', timeout_s)
    if not read.ok:
        result.transport_failures.append(FeedCheck(
            'health_route_answers', False, f'GET {HEALTH_ROUTE}: {read.detail}'))
        return
    result.identity = parse_identity(read.payload, pipeline_id)


def read_build(root: str, timeout_s: float, result: FeedProbeResult) -> None:
    """
    Ask the producer which BUILD is running, without sending a token.

    Args:
        root: Producer base URL, without a trailing slash
        timeout_s: Request timeout
        result: Result being accumulated
    """
    result.routes_used.append(RouteCall('GET', BUILD_ROUTE))
    read = fetch_json(f'{root}{BUILD_ROUTE}', '', timeout_s)
    if not read.ok:
        result.build = ProducerBuild(offered=False, detail=read.detail)
        return

    payload = read.payload
    result.build = ProducerBuild(
        offered=True,
        version=payload.get('version') or '',
        commit=payload.get('commit') or '',
        committed_at=parse_instant(payload.get('committed_at')),
        dirty=payload.get('dirty'),
        started_at=parse_instant(payload.get('started_at')))


def parse_identity(payload: Dict[str, Any], pipeline_id: str) -> ProducerIdentity:
    """
    Read the producer identity out of one health document.

    The id binds and the name does not: the id fingerprints their database cluster and is
    fixed at its creation, while the name is resolved from a mapping on their machine and
    may be renamed at any time. A name the producer could not resolve degrades to 'unknown'
    WITHOUT the id losing its meaning.

    Args:
        payload: The decoded health document
        pipeline_id: Source whose evaluation cadence is wanted

    Returns:
        The identity it reported
    """
    journal_id = payload.get('journal_id') or None
    environment = payload.get('environment') or UNRESOLVED_JOURNAL_NAME
    budget = payload.get('budget') or {}
    return ProducerIdentity(
        journal_id=journal_id,
        environment=environment if journal_id else '',
        engine_version=payload.get('version') or '',
        pass_timeout_s=payload.get('pass_timeout_seconds'),
        cadence_seconds=read_cadence(payload, pipeline_id),
        budget_suspended=bool(budget.get('suspended')))


def read_cadence(payload: Dict[str, Any], pipeline_id: str) -> Optional[float]:
    """
    How often the producer evaluates the source this run reads.

    Args:
        payload: The decoded health document
        pipeline_id: Source whose worker is wanted

    Returns:
        The producer's interval in seconds, or None when it names no worker for us
    """
    wanted = f'eval:{pipeline_id}'
    for worker in payload.get('workers') or []:
        if worker.get('name') == wanted:
            interval = worker.get('interval_seconds')
            return float(interval) if interval is not None else None
    return None


def parse_instant(raw) -> Optional[datetime]:
    """
    Normalize one of the producer's ISO stamps to a tz-aware UTC datetime (§9).

    Args:
        raw: The stamp as it arrived

    Returns:
        The instant in UTC, or None when it was absent or unreadable
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
