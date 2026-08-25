"""
FiniexTestingIDE - Signal Feed Observer
Reads what the release-gate certificate needs from the running producer (#466).

Deliberately the THIN half of the certificate. Everything it produces is data — envelopes,
an identity, a list of routes called — and every verdict about that data lives in
SignalFeedContractValidator. That is what lets the stream (#468) add a second observer
without touching a single assertion.

Two properties are structural rather than promised:

- It calls only the producer's free routes. `POST /v1/pipelines/{id}/run` turns a request
  into LLM spend, so no method here can reach it, and every call is recorded so the
  certificate can state what the run touched instead of asserting it did nothing.
- `/v1/health` and `/v1/build` are read WITHOUT a token, `/latest` WITH one. That separation
  is the producer's own contract — the first two are their open routes — and it is what
  keeps the failure modes apart: a failure on the open routes is the ADDRESS, a failure on
  /latest alone is the CREDENTIAL, and a check that merges the two sends the operator to
  the wrong system.
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from python.framework.signal_data.signal_http_reader import fetch_json
from python.framework.types.config_types.sentiment_config_types import ActiveProducer
from python.framework.types.signal_certificate_types import (
    FeedCheck,
    FeedObservation,
    FeedProbeResult,
    ProducerBuild,
    ProducerIdentity,
    RouteCall,
)
from python.framework.types.signal_data_types import SignalSnapshot

# The only routes this observer may touch. Both are free on the producer side; the paid
# run route is absent from the module, not merely unused.
HEALTH_ROUTE = '/v1/health'
BUILD_ROUTE = '/v1/build'
LATEST_ROUTE_TEMPLATE = '/v1/pipelines/{pipeline_id}/latest'

# What the producer calls a journal whose name its own mapping could not resolve.
UNRESOLVED_JOURNAL_NAME = 'unknown'


class SignalFeedObserver:
    """
    Performs one certificate run's reads against the producer and returns them raw.

    Holds no verdicts and writes no artifact — the validator judges, the certificate
    records.
    """

    def __init__(
        self,
        producer: ActiveProducer,
        pipeline_id: str,
        timeout_s: float = 20.0,
    ):
        """
        Initialize the observer.

        Args:
            producer: Active endpoint with its resolved credential
            pipeline_id: Source to read envelopes from
            timeout_s: Per-request timeout
        """
        self._producer = producer
        self._pipeline_id = pipeline_id
        self._timeout_s = timeout_s
        self._root = producer.base_url.rstrip('/')

    def observe(
        self,
        observation_count: int = 2,
        gap_seconds: float = 15.0,
    ) -> FeedProbeResult:
        """
        Read the producer's identity, then one envelope per observation.

        Two observations are the minimum that can say anything about the series: one read
        cannot show that seq moves forward or that the epoch held. The gap between them is
        the operator's choice — a short gap proves stability, a gap longer than the
        producer's cadence additionally samples the cadence itself.

        Args:
            observation_count: How many envelopes to read
            gap_seconds: Pause between consecutive reads

        Returns:
            Everything the run read, with its transport failures recorded as checks
        """
        result = FeedProbeResult(
            endpoint_name=self._producer.name,
            base_url=self._root,
            credential_source=self._producer.credential.describe_source(),
            credential_configured=self._producer.credential.is_configured(),
            pipeline_id=self._pipeline_id)

        self._read_identity(result)
        self._read_build(result)
        for index in range(observation_count):
            if index > 0 and gap_seconds > 0:
                time.sleep(gap_seconds)
            self._read_envelope(result, index)
        return result

    # ============================================
    # Internals
    # ============================================

    def _read_identity(self, result: FeedProbeResult) -> None:
        """
        Ask the producer which journal it writes into, without sending a token.

        Args:
            result: Result being accumulated
        """
        route = HEALTH_ROUTE
        result.routes_used.append(RouteCall('GET', route))
        read = fetch_json(f'{self._root}{route}', '', self._timeout_s)
        if not read.ok:
            result.transport_failures.append(FeedCheck(
                'health_route_answers', False, f'GET {route}: {read.detail}'))
            return
        result.identity = self._parse_identity(read.payload)

    def _read_build(self, result: FeedProbeResult) -> None:
        """
        Ask the producer which BUILD is running, without sending a token.

        Public by their default, like /v1/health, but behind a switch on their side — so a
        refusal here is a policy answer and not a fault. It is recorded as 'not offered'
        rather than as a transport failure, because a certificate that failed on their
        configuration choice would be asserting something that was never promised.

        Args:
            result: Result being accumulated
        """
        result.routes_used.append(RouteCall('GET', BUILD_ROUTE))
        read = fetch_json(f'{self._root}{BUILD_ROUTE}', '', self._timeout_s)
        if not read.ok:
            result.build = ProducerBuild(offered=False, detail=read.detail)
            return

        payload = read.payload
        result.build = ProducerBuild(
            offered=True,
            version=payload.get('version') or '',
            commit=payload.get('commit') or '',
            committed_at=_parse_instant(payload.get('committed_at')),
            dirty=payload.get('dirty'),
            started_at=_parse_instant(payload.get('started_at')))

    def _read_envelope(self, result: FeedProbeResult, index: int) -> None:
        """
        Read one envelope with the bearer token, parse it, and stamp receipt.

        Args:
            result: Result being accumulated
            index: Observation number, for the failure message
        """
        route = LATEST_ROUTE_TEMPLATE.format(pipeline_id=self._pipeline_id)
        result.routes_used.append(RouteCall('GET', route))
        read = fetch_json(
            f'{self._root}{route}', self._producer.credential.token, self._timeout_s)
        # Receipt time, not event time: this is ts_init and measures OUR observation (§9).
        fetched_at = datetime.now(timezone.utc)

        if not read.ok:
            name = ('credential_accepted' if read.credential_rejected
                    else f'latest_route_answers_{index}')
            result.transport_failures.append(FeedCheck(
                name, False, f'GET {route}: {read.detail}'))
            return

        envelope = read.payload
        try:
            snapshot = SignalSnapshot.model_validate({
                **envelope,
                'collected_msc': int(fetched_at.timestamp() * 1000),
            })
        except Exception as error:   # noqa: BLE001 — the refusal IS the finding
            # Keep the payload: the shape checks run over it anyway, so the certificate
            # names the field that disagreed instead of only reporting that OUR reader
            # said no. Getting that backwards once cost a day of blaming their outage.
            result.unparsed_envelopes.append(envelope)
            result.transport_failures.append(FeedCheck(
                'envelope_parses_through_production_reader', False,
                f'the production reader refused the envelope: '
                f'{type(error).__name__} — {error}'))
            return

        result.observations.append(FeedObservation(
            envelope=envelope,
            snapshot=snapshot,
            fetched_at=fetched_at,
            frame_bytes=len(json.dumps(envelope).encode('utf-8'))))

    def _parse_identity(self, payload: Dict[str, Any]) -> ProducerIdentity:
        """
        Read the producer identity out of one health document.

        Reads the same document SignalHealthProbe reads during a live session; that one
        additionally tracks change-over-time (a journal swapped mid-session, a budget
        suspension appearing), which a one-shot certificate run has no use for.

        Args:
            payload: The decoded health document

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
            cadence_seconds=self._read_cadence(payload),
            budget_suspended=bool(budget.get('suspended')))

    def _read_cadence(self, payload: Dict[str, Any]) -> Optional[float]:
        """
        How often the producer evaluates the source this run reads.

        Args:
            payload: The decoded health document

        Returns:
            The producer's interval in seconds, or None when it names no worker for us
        """
        wanted = f'eval:{self._pipeline_id}'
        for worker in payload.get('workers') or []:
            if worker.get('name') == wanted:
                interval = worker.get('interval_seconds')
                return float(interval) if interval is not None else None
        return None


def _parse_instant(raw) -> Optional[datetime]:
    """
    Normalize one of the producer's ISO stamps to a tz-aware UTC datetime (§9).

    Args:
        raw: ISO 8601 string, or None

    Returns:
        The instant in UTC, or None when the producer sent nothing readable
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None
