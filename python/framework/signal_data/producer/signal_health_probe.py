"""
FiniexTestingIDE - Signal Health Probe
Asks the producer engine which journal it writes into (#141 Part 2a).

Nothing on an envelope says which store it came from. Two producer instances share a
schema, a pipeline_id and a seq range, so every measurement taken this way looks the
same whether it came from a development instance or from the series a release is
certified against. The producer answers the question on /v1/health and nowhere else.

Runs on its own thread rather than inside the transport's read loop: its cadence is half
an hour against a connection that may sit quiet for the producer's whole beat, and folding
it in would make it inherit the transport's back-off — a producer whose store is briefly
unavailable would also stop being asked who it is.
"""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.signal_data.producer.signal_producer_reads import read_cadence
from python.framework.types.config_types.sentiment_config_types import (
    SentimentHealthConfig,
    SentimentSourceConfig,
)
from python.framework.types.decision_logic_types import AwarenessLevel
from python.framework.types.signal_data_types import SignalHealthStatus

# What the producer calls a journal whose name its own mapping could not resolve. The
# id still binds — only the label is missing, which is not a fault worth alarming on.
UNRESOLVED_JOURNAL_NAME = 'unknown'

# How far the producer's reported cadence may differ from the configured one before it is
# worth saying so. Both sides are operator-set integers, so this only absorbs float noise.
CADENCE_TOLERANCE = 0.01


class SignalHealthProbe:
    """
    Polls the producer's health endpoint and holds the journal identity it reports.

    Owned by the transport it accompanies, because it borrows that transport's address:
    the question is which journal is delivering envelopes, not whether some engine is up.
    """

    def __init__(
        self,
        config: SentimentHealthConfig,
        base_url: str,
        logger: ScenarioLogger,
        api_token: str = '',
        pipeline_id: str = '',
        source: Optional[SentimentSourceConfig] = None,
    ):
        """
        Initialize the health probe.

        Args:
            config: Probe cadence and timeout
            base_url: Address of the engine the transport consumes from
            logger: Session logger — the identity belongs in the run's artifacts, not
                only on screen, so a finished run can still say where its data came from
            api_token: Bearer token; empty means send no Authorization header
            pipeline_id: Source being consumed — names the producer worker whose cadence
                describes our feed; empty skips the cadence reading
            source: What we have configured about that source, so a producer that changed
                its cadence is reported rather than silently believed to still match
        """
        self._config = config
        self._logger = logger
        self._api_token = api_token
        self._pipeline_id = pipeline_id
        self._source = source
        self._on_event: Optional[Callable[[str, AwarenessLevel], None]] = None
        self._url = f"{base_url.rstrip('/')}/v1/health"

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._status = SignalHealthStatus()
        self._warned_unidentified = False
        self._warned_cadence = False

    def set_event_sink(
        self, on_event: Callable[[str, AwarenessLevel], None]
    ) -> None:
        """
        Route identity moments onto the transport's tape.

        Set by the owning transport rather than passed in, because the transport builds
        after the probe it owns.

        Args:
            on_event: Sink taking a message and its severity
        """
        self._on_event = on_event

    def start(self) -> None:
        """Probe once immediately, then on the configured cadence."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name='signal-health', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop probing and wait for the thread to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._config.request_timeout_s + 2.0)
            self._thread = None

    def get_status(self) -> SignalHealthStatus:
        """
        Snapshot of the producer identity.

        Returns:
            The last known identity — unidentified before the first answer
        """
        with self._lock:
            return SignalHealthStatus(
                journal_id=self._status.journal_id,
                journal_name=self._status.journal_name,
                engine_version=self._status.engine_version,
                pass_timeout_s=self._status.pass_timeout_s,
                probed_at=self._status.probed_at,
                journal_changed=self._status.journal_changed,
                probe_errors=self._status.probe_errors,
                producer_cadence_s=self._status.producer_cadence_s,
                budget_suspended=self._status.budget_suspended,
                budget_reason=self._status.budget_reason,
            )

    def probe_once(self) -> None:
        """Ask the producer who it is and record the answer."""
        payload = self._fetch()
        journal_id = payload.get('journal_id') or None
        journal_name = payload.get('environment') or UNRESOLVED_JOURNAL_NAME

        budget = payload.get('budget') or {}
        suspended = bool(budget.get('suspended'))
        cadence = self._read_cadence(payload)

        with self._lock:
            previous = self._status.journal_id
            changed = previous is not None and journal_id != previous
            was_suspended = self._status.budget_suspended
            self._status.journal_id = journal_id
            self._status.journal_name = journal_name if journal_id else ''
            self._status.engine_version = payload.get('version') or ''
            self._status.pass_timeout_s = payload.get('pass_timeout_seconds')
            self._status.probed_at = datetime.now(timezone.utc)
            self._status.producer_cadence_s = cadence
            self._status.budget_suspended = suspended
            self._status.budget_reason = budget.get('reason')
            if changed:
                self._status.journal_changed = True
            first_answer = previous is None and not changed

        if changed:
            self._report_change(previous, journal_id, journal_name)
        elif first_answer:
            self._report_first(journal_id, journal_name)
        if suspended != was_suspended:
            self._report_budget(suspended, budget.get('reason'))
        self._check_cadence(cadence)

    def _read_cadence(self, payload: dict) -> Optional[float]:
        """
        How often the producer evaluates the source we consume.

        Delegated so the live probe and the certificate observer cannot disagree about what
        a health document means — the same reason the identity read is shared. A second
        reading of an agreed contract is the mistake this project has now paid for three
        times.

        Args:
            payload: The decoded health document

        Returns:
            The producer's interval in seconds, or None when it names no worker for us
        """
        if not self._pipeline_id:
            return None
        return read_cadence(payload, self._pipeline_id)

    def _check_cadence(self, cadence: Optional[float]) -> None:
        """
        Report a producer cadence that no longer matches what we configured.

        The configured value drives our staleness threshold, so a producer that slowed
        down turns a healthy feed into one that keeps tripping the contract — and a
        producer that sped up hides a genuine outage inside the tolerance.

        Args:
            cadence: The producer's reported interval in seconds
        """
        if cadence is None or self._source is None or self._warned_cadence:
            return
        configured = self._source.cadence_minutes * 60.0
        if configured <= 0 or abs(cadence - configured) <= configured * CADENCE_TOLERANCE:
            return
        self._warned_cadence = True
        self._emit(f'cadence {cadence:.0f}s vs configured {configured:.0f}s',
                   AwarenessLevel.NOTICE)
        self._logger.warning(
            f'📡 Producer evaluates {self._pipeline_id} every {cadence:.0f}s, but '
            f'sentiment_config.json has {configured:.0f}s. The configured value drives '
            f'the staleness threshold — reconcile the two.')

    def _report_budget(self, suspended: bool, reason: Optional[str]) -> None:
        """
        Report that the producer started or stopped evaluating for budget reasons.

        A suspended budget reaches us as silence and nothing else: the transport stays
        healthy, envelopes simply stop. Naming the cause here is what separates "the
        producer cannot pay for calls" from "the producer died".

        The flag means the producer's LLM provider refused a call for quota — NOT that
        the producer crossed a budget line of its own (corrected by the producer
        2026-08-24; their day line is warn-only and suspends nothing).

        Args:
            suspended: Whether the producer is currently withholding evaluations
            reason: What the producer says about it
        """
        if suspended:
            detail = f': {reason}' if reason else ''
            self._emit('producer budget suspended', AwarenessLevel.ALERT)
            self._logger.warning(
                f'📡 Producer budget suspended{detail} — its LLM provider refused calls '
                f'for quota, so it has stopped evaluating. The feed falls silent while '
                f'the transport stays healthy.')
            return
        self._emit('producer budget resumed', AwarenessLevel.INFO)
        self._logger.info('📡 Producer budget resumed — evaluations continue.')

    def _run(self) -> None:
        """Probe until stopped, treating an unreachable producer as non-fatal."""
        while not self._stop.is_set():
            try:
                self.probe_once()
            except urllib.error.HTTPError as error:
                # /v1/health is the producer's one documented no-token route, so a
                # credential status here is a misconfigured address rather than a missing
                # token — worth saying out loud instead of counting as a probe error.
                if error.code in (401, 403):
                    self._emit(f'health route rejected credential ({error.code})',
                               AwarenessLevel.ALERT)
                    self._logger.warning(
                        f'📡 The producer health route answered {error.code}. That route is '
                        f'documented as reachable without a token, so this points at the '
                        f'configured address rather than at the credential.')
                else:
                    with self._lock:
                        self._status.probe_errors += 1
                    self._logger.debug(f'📡 Producer health probe failed: {error}')
            except Exception as error:   # noqa: BLE001 — identity is never worth a crash
                with self._lock:
                    self._status.probe_errors += 1
                self._logger.debug(f'📡 Producer health probe failed: {error}')
            self._stop.wait(self._config.interval_s)

    def _fetch(self) -> dict:
        """
        Read the producer's health document.

        Returns:
            The decoded response
        """
        request = urllib.request.Request(self._url)
        if self._api_token:
            request.add_header('Authorization', f'Bearer {self._api_token}')
        with urllib.request.urlopen(
                request, timeout=self._config.request_timeout_s) as response:
            return json.loads(response.read().decode('utf-8'))

    def _report_first(self, journal_id: Optional[str], journal_name: str) -> None:
        """
        Record the identity a session is consuming from.

        Args:
            journal_id: Cluster fingerprint, None when the producer named none
            journal_name: The producer's label for it
        """
        if journal_id is None:
            if self._warned_unidentified:
                return
            self._warned_unidentified = True
            self._emit('producer journal unidentified', AwarenessLevel.NOTICE)
            self._logger.warning(
                '📡 Producer named no journal — it has no store attached or cannot read '
                'its own identifier. This session cannot be certified against a series.')
            return
        with self._lock:
            version = self._status.engine_version or 'unknown'
            timeout = self._status.pass_timeout_s
        timeout_str = f'{timeout:.0f}s' if timeout is not None else 'unknown'
        self._emit(f'journal {journal_id} ({journal_name})', AwarenessLevel.INFO)
        self._logger.info(
            f'📡 Producer journal {journal_id} ({journal_name}) · engine {version} · '
            f'pass timeout {timeout_str}')

    def _report_change(
        self, previous: Optional[str], journal_id: Optional[str], journal_name: str
    ) -> None:
        """
        Report that the producer identity changed mid-session.

        The cursor this session built — the last (stream_epoch, seq) it accepted — was
        built against the previous journal and means nothing in a different one. Logged
        as an error so it reaches the session summary (§35) rather than only the screen.

        Args:
            previous: The identity seen until now
            journal_id: The identity reported now, None when the producer named none
            journal_name: The producer's label for it
        """
        current = f'{journal_id} ({journal_name})' if journal_id else 'unidentified'
        self._emit(f"journal CHANGED {previous} → {journal_id or 'none'}",
                   AwarenessLevel.ALERT)
        self._logger.error(
            f'📡 Producer journal changed mid-session: {previous} → {current}. The '
            f'sequence position accepted so far belongs to the previous journal and '
            f'does not carry over — measurements from this session span two series.')

    def _emit(self, message: str, level: AwarenessLevel) -> None:
        """
        Forward a transport moment to the tape, when one is attached.

        Args:
            message: Transport fact
            level: Display severity
        """
        if self._on_event is not None:
            self._on_event(message, level)
