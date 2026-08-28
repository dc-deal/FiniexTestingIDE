"""
FiniexTestingIDE - Signal Feed Stream Observer
One certificate run's reads over the PUSH transport (#468, #466).

Emits the same `FeedProbeResult` the interim poll observer emitted, so every assertion
above it applied untouched across the swap — that was the promise the observer split was
made for, and it is what let the pull observer be deleted (2026-08-28) without a single
assertion changing. Where the envelopes come from is recorded in the result rather than
declared by a constant.

The envelopes come from the REAL transport, not from a second SSE client written to watch
the first. A stream frame arrives exactly once, so an observer cannot simply read again the
way the poll observer read `/latest` twice; the alternative would be a parallel client
with its own frame parsing, reconnect and cursor — one more derivation of a contract this
project has already derived twice too often. Instead the transport carries an optional
frame recorder, which keeps each raw payload beside its parsed form. That matters because
half the contract is about the WIRE: a field's absence, its wire type and its location are
all unanswerable once the payload has become a model.

`GET /v1/pipelines` is read for a structural reason rather than for completeness — the
transport cannot start without the keep-alive interval its watchdog measures against, so a
stream observation is impossible without it.
"""

import time

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.signal_data.producer.signal_pipelines_reader import (
    PIPELINES_ROUTE,
    fetch_pipeline_registry,
)
from python.framework.signal_data.producer.signal_producer_reads import (
    read_build,
    read_identity,
)
from python.framework.signal_data.transport.signal_frame_recorder import SignalFrameRecorder
from python.framework.signal_data.transport.signal_inbox import SignalInbox
from python.framework.signal_data.transport.signal_stream_source import SignalStreamSource
from python.framework.types.config_types.sentiment_config_types import (
    ActiveProducer,
    SentimentStreamConfig,
)
from python.framework.types.signal_certificate_types import (
    FeedCheck,
    FeedProbeResult,
    RouteCall,
)
from python.framework.types.signal_data_types import SignalTransportKind

STREAM_ROUTE_TEMPLATE = '/v1/stream/{pipeline_id}'

# How long the connection is held when the caller names no duration. Long enough to cross
# one keep-alive at the producer's 20 s beat, so the observation covers a quiet stretch and
# not only the connect snapshot.
DEFAULT_OBSERVE_SECONDS = 25.0

# How many envelopes the connect asks for. TWO is the minimum that can say anything about a
# series — one position cannot show that seq moved forward or that the epoch held, and a
# check that cannot be evaluated must not report success. Waiting for a second envelope
# instead would mean holding the connection past their ten-minute cadence for a fact the
# connect snapshot can carry immediately, and these are real frames over the real transport
# either way: their contract offers `history=N` for exactly this.
DEFAULT_CONNECT_HISTORY = 3


class SignalFeedStreamObserver:
    """
    Performs one certificate run's reads over the stream and returns them raw.

    Holds no verdicts and writes no artifact — the validator judges, the certificate
    records.
    """

    def __init__(
        self,
        producer: ActiveProducer,
        stream_config: SentimentStreamConfig,
        logger: ScenarioLogger,
        timeout_s: float = 10.0,
    ):
        """
        Initialize the observer.

        Args:
            producer: Active endpoint with its resolved credential
            stream_config: The stream transport's own settings, carrying the pipeline id
            logger: Logger the transport reports through
            timeout_s: Per-request timeout for the token-free reads
        """
        self._producer = producer
        self._stream_config = stream_config
        self._logger = logger
        self._timeout_s = timeout_s
        self._root = producer.base_url.rstrip('/')
        self._pipeline_id = stream_config.pipeline_id

    def observe(self, seconds: float = DEFAULT_OBSERVE_SECONDS,
                history: int = DEFAULT_CONNECT_HISTORY) -> FeedProbeResult:
        """
        Read the producer's identity and build, then hold the stream and record what came.

        Args:
            seconds: How long to hold the connection
            history: Envelopes to ask for on connect; two is the minimum that can say
                anything about a series

        Returns:
            Everything the run read, with its transport failures recorded as checks
        """
        result = FeedProbeResult(
            transport=SignalTransportKind.STREAM,
            endpoint_name=self._producer.name,
            base_url=self._root,
            credential_source=self._producer.credential.describe_source(),
            credential_configured=self._producer.credential.is_configured(),
            pipeline_id=self._pipeline_id)

        read_identity(self._root, self._pipeline_id, self._timeout_s, result)
        read_build(self._root, self._timeout_s, result)

        settings = self._read_registry(result)
        if settings is None:
            return result
        self._read_stream(result, settings, seconds, history)
        return result

    def _read_registry(self, result: FeedProbeResult):
        """
        Read the pipeline registry — the stream's own precondition, not a nicety.

        Args:
            result: Result being accumulated

        Returns:
            The engine-wide stream settings, or None when the run cannot proceed
        """
        result.routes_used.append(RouteCall('GET', PIPELINES_ROUTE))
        registry = fetch_pipeline_registry(self._producer, self._timeout_s)
        if not registry.ok:
            name = ('credential_accepted' if registry.credential_rejected
                    else 'pipelines_route_answers')
            result.transport_failures.append(FeedCheck(
                name, False, f'GET {PIPELINES_ROUTE}: {registry.detail}'))
            return None
        if registry.stream is None:
            result.transport_failures.append(FeedCheck(
                'stream_settings_served', False,
                f'{PIPELINES_ROUTE} carries no engine-wide stream block — the transport '
                f'has no keep-alive interval to measure its watchdog against'))
            return None
        if self._pipeline_id not in registry.pipelines:
            known = ', '.join(sorted(registry.pipelines)) or '(none registered)'
            result.transport_failures.append(FeedCheck(
                'pipeline_registered', False,
                f"'{self._pipeline_id}' is not registered with this producer. "
                f'Known: {known}'))
            return None
        return registry.stream

    def _read_stream(self, result: FeedProbeResult, settings, seconds: float,
                     history: int) -> None:
        """
        Hold the stream open and turn what it delivered into observations.

        Cursor-less on purpose: an observation claims no position, because a certificate
        run that advanced a session's cursor would consume envelopes the session it
        certifies still needs.

        Args:
            result: Result being accumulated
            settings: The producer's served stream values
            seconds: How long to hold the connection
            history: Envelopes to ask for on connect
        """
        route = STREAM_ROUTE_TEMPLATE.format(pipeline_id=self._pipeline_id)
        result.routes_used.append(RouteCall('GET', route))

        recorder = SignalFrameRecorder()
        source = SignalStreamSource(
            config=self._stream_config,
            producer=self._producer,
            stream_settings=settings,
            signal_kind='llm_sentiment',
            inbox=SignalInbox(),
            logger=self._logger,
            frame_recorder=recorder,
            connect_history=history)
        source.start()
        try:
            time.sleep(seconds)
        finally:
            source.stop()

        stats = source.get_transport_stats()
        for observation in recorder.get_observations():
            result.observations.append(observation)

        if not result.observations:
            result.transport_failures.append(FeedCheck(
                'stream_delivered_an_envelope', False,
                f'GET {route}: the connection reached state {stats.state!r} and delivered '
                f'no envelope in {seconds:.0f}s'))
        if stats.contract_errors:
            result.transport_failures.append(FeedCheck(
                'every_frame_parses_through_production_reader', False,
                f'{stats.contract_errors} frame(s) the producer sent could not be read by '
                f'the production reader'))

