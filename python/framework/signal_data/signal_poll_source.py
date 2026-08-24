"""
FiniexTestingIDE - Signal Poll Source
Interim live signal transport: polls the producer's /latest endpoint (#141 Part 2a).

Deliberately the throwaway half. A poll returns whatever the producer stored last, which
is up to a full producer interval old — measured against the live engine at 101.8 s. The
stream removes exactly that, and when it exists this unit is replaced. What it feeds does
not change: it stamps a receipt time, parses with the same reader the archive uses, and
drops the result in the SignalInbox. Everything behind the inbox is the permanent path.

Runs on its own thread because the loop must never wait on a network call.
"""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from collections import deque
from typing import Deque, Optional, Tuple

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.signal_data.signal_health_probe import SignalHealthProbe
from python.framework.signal_data.signal_inbox import SignalInbox
from python.framework.signal_data.signal_observed_accumulator import SignalObservedAccumulator
from python.framework.types.autotrader_types.autotrader_display_types import (
    SignalTransportEvent, SignalTransportStats)
from python.framework.types.config_types.sentiment_config_types import SentimentPollConfig
from python.framework.types.decision_logic_types import AwarenessLevel
from python.framework.types.signal_data_types import SignalHealthStatus, SignalSnapshot

# The producer serves /latest from its store and never runs a pass for it. When the store
# cannot answer, it says so in the contract's error envelope instead of degrading to a
# paid run. That is the one condition worth waiting out rather than hammering.
STORE_UNAVAILABLE_ERROR = 'VECTOR_STORE_ERROR'

# How many transport moments the operator panel keeps. The tail is what a human
# reads; the total is what tells them the tail is a tail.
TAPE_LENGTH = 8


class SignalPollSource:
    """
    Polls one producer pipeline and deposits new envelopes in the inbox.

    Only genuinely new envelopes are enqueued: the producer republishes the same stored
    envelope until its next pass, so the poller tracks the last (stream_epoch, seq) it saw
    and stays quiet in between. The provider would deduplicate anyway — this just keeps
    the inbox honest about what actually arrived.
    """

    def __init__(
        self,
        config: SentimentPollConfig,
        signal_kind: str,
        inbox: SignalInbox,
        logger: ScenarioLogger,
        api_token: str = '',
        health_probe: Optional[SignalHealthProbe] = None,
        observed: Optional[SignalObservedAccumulator] = None,
    ):
        """
        Initialize the poll source.

        Args:
            config: Poll transport configuration
            signal_kind: Payload kind the snapshots are filed under
            inbox: Hand-off buffer drained by the loop
            logger: Session logger — operator-relevant failures belong here (§35)
            api_token: Bearer token; empty means send no Authorization header
            health_probe: Optional producer-identity probe, started and stopped with
                this transport because it borrows this transport's address
            observed: Optional accumulator recording what the arriving envelopes state
                about themselves — the live half of the signal report, which has no
                archive to read those facts out of
        """
        self._config = config
        self._signal_kind = signal_kind
        self._inbox = inbox
        self._logger = logger
        self._api_token = api_token
        self._health_probe = health_probe
        self._observed = observed
        self._url = (f"{config.base_url.rstrip('/')}"
                     f"/v1/pipelines/{config.pipeline_id}/latest")

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_identity: Optional[Tuple[Optional[int], Optional[int]]] = None
        self._polls = 0
        self._enqueued = 0
        self._degraded = 0
        self._transport_errors = 0
        # Operator view (#141 Part 2a Phase 3b). A dead feed and a quiet market look
        # identical on screen without this: the signal VALUES keep displaying their last
        # known state either way, so only the transport can say whether anything still
        # arrives. Bounded — the tape shows the tail, the counter shows the total.
        self._state = 'live'
        self._last_seq: Optional[int] = None
        self._last_epoch: Optional[int] = None
        self._last_envelope_at: Optional[datetime] = None
        self._tape: Deque[SignalTransportEvent] = deque(maxlen=TAPE_LENGTH)
        self._total_events = 0
        self._stats_lock = threading.Lock()
        if health_probe is not None:
            health_probe.set_event_sink(self._record)

    def start(self) -> None:
        """Start polling on a background thread."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name='signal-poll', daemon=True)
        self._thread.start()
        if self._health_probe is not None:
            self._health_probe.start()
        self._record(f"connected — {self._config.interval_s:.0f}s cadence")
        self._logger.info(
            f"📡 Signal poll started: {self._url} every {self._config.interval_s:.0f}s")

    def stop(self) -> None:
        """Stop polling and wait for the thread to finish."""
        self._stop.set()
        if self._health_probe is not None:
            self._health_probe.stop()
        if self._thread is not None:
            self._thread.join(timeout=self._config.request_timeout_s + 2.0)
            self._thread = None
        self._logger.info(
            f"📡 Signal poll stopped: {self._polls} polls, {self._enqueued} new envelopes, "
            f"{self._degraded} degraded responses")

    def get_stats(self) -> Tuple[int, int, int]:
        """
        Session counters.

        Returns:
            (polls made, envelopes enqueued, degraded responses)
        """
        with self._stats_lock:
            return self._polls, self._enqueued, self._degraded

    def get_transport_stats(self) -> SignalTransportStats:
        """
        Snapshot of the transport for the operator panel.

        Read from the loop thread while the transport thread writes, hence the lock: a
        half-updated panel is worse than a slightly old one.

        Returns:
            The current transport view
        """
        health = (self._health_probe.get_status()
                  if self._health_probe is not None else SignalHealthStatus())
        with self._stats_lock:
            return SignalTransportStats(
                configured=True,
                state=self._state,
                source=self._signal_kind,
                last_seq=self._last_seq,
                stream_epoch=self._last_epoch,
                last_envelope_at=self._last_envelope_at,
                envelopes_received=self._enqueued,
                degraded_responses=self._degraded,
                transport_errors=self._transport_errors,
                tape=list(self._tape),
                total_events=self._total_events,
                health=health,
            )

    def _record(self, message: str, level: AwarenessLevel = AwarenessLevel.INFO) -> None:
        """
        Append a transport moment to the tape.

        Args:
            message: Transport fact — never a signal value, those have their own panel
            level: Display severity
        """
        with self._stats_lock:
            self._tape.append(SignalTransportEvent(
                message=message, at=datetime.now(timezone.utc), level=level))
            self._total_events += 1

    def _run(self) -> None:
        """Poll until stopped, backing off when the producer cannot serve from its store."""
        while not self._stop.is_set():
            wait_s = self._config.interval_s
            try:
                if self._poll_once():
                    wait_s = self._config.degraded_backoff_s
            except Exception as error:   # noqa: BLE001 — a transport fault never stops the loop
                with self._stats_lock:
                    self._transport_errors += 1
                    self._state = 'error'
                self._record(f"transport failed: {type(error).__name__}",
                             AwarenessLevel.ALERT)
                self._logger.warning(f"📡 Signal poll failed: {error}")
            self._stop.wait(wait_s)

    def _poll_once(self) -> bool:
        """
        Fetch once, enqueue when the envelope is new.

        Returns:
            True when the producer reported it could not serve from its store (back off)
        """
        with self._stats_lock:
            self._polls += 1
        payload = self._fetch()

        if self._is_store_unavailable(payload):
            with self._stats_lock:
                self._degraded += 1
                self._state = 'degraded'
            detail = (payload.get('errors') or [{}])[0].get('message', '')
            self._record(f"producer store unavailable — backing off "
                         f"{self._config.degraded_backoff_s:.0f}s", AwarenessLevel.NOTICE)
            self._logger.warning(
                f"📡 Producer store unavailable, backing off "
                f"{self._config.degraded_backoff_s:.0f}s: {detail}")
            return True

        # The producer served an envelope, so whatever fault the state carries is over.
        # Recovery belongs HERE and not where a new envelope lands: the producer's beat is
        # far longer than the poll interval, so most polls legitimately return an
        # already-seen envelope and leave through the early return below. Recovering only
        # on arrival left a transient fault on the operator panel until the producer
        # happened to publish again — a healthy feed reading as a dead one, which is the
        # exact misreading this panel exists to prevent.
        with self._stats_lock:
            self._state = 'live'

        received = datetime.now(timezone.utc)
        snapshot = SignalSnapshot.model_validate(
            {**payload, 'collected_msc': int(received.timestamp() * 1000)})

        identity = (snapshot.stream_epoch, snapshot.seq)
        if identity == self._last_identity:
            return False
        self._last_identity = identity

        self._inbox.put(self._signal_kind, [snapshot])
        if self._observed is not None:
            self._observed.observe(snapshot)
        with self._stats_lock:
            self._enqueued += 1
            self._last_seq = snapshot.seq
            self._last_epoch = snapshot.stream_epoch
            self._last_envelope_at = snapshot.get_resolution_key()
        # "<trigger> pass", never the bare word: 'breaking' here is why the PASS ran, not
        # the model's verdict for a symbol. The two are independent — an out-of-band pass
        # can carry is_breaking=False for the symbol we trade — and a tape that renders
        # them alike invites reading one as the other.
        trigger = snapshot.trigger_reason or 'unknown-trigger'
        self._record(
            f"seq {snapshot.seq if snapshot.seq is not None else '—'} · {trigger} pass")
        age_s = (received - snapshot.get_resolution_key()).total_seconds()
        self._logger.debug(
            f"📡 Signal envelope seq={snapshot.seq} epoch={snapshot.stream_epoch} "
            f"trigger={snapshot.trigger_reason or 'unknown'} age={age_s:.1f}s")
        return False

    def _fetch(self) -> dict:
        """
        Read the producer's latest envelope.

        Returns:
            The decoded envelope

        Raises:
            urllib.error.URLError: On a transport failure — the caller logs and retries
        """
        request = urllib.request.Request(self._url)
        if self._api_token:
            request.add_header('Authorization', f'Bearer {self._api_token}')
        with urllib.request.urlopen(
                request, timeout=self._config.request_timeout_s) as response:
            return json.loads(response.read().decode('utf-8'))

    @staticmethod
    def _is_store_unavailable(payload: dict) -> bool:
        """
        Whether the producer answered that it could not serve from its store.

        This replaces an earlier, wrong signal: a response carrying
        trigger_reason 'external' used to be read as "it just ran". Since the producer's
        GET can no longer run a pass, that inference is impossible in one direction and
        false in the other — an operator's POST /run is persisted and legitimately becomes
        the newest envelope, which would have made this back off against a healthy signal.

        Args:
            payload: The decoded envelope

        Returns:
            True when the store could not supply an envelope
        """
        if payload.get('status') != 'error':
            return False
        errors = payload.get('errors') or []
        return bool(errors) and errors[0].get('type') == STORE_UNAVAILABLE_ERROR
