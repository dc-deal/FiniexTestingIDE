"""
FiniexTestingIDE - Signal Health Probe
Asks the producer engine which journal it writes into (#141 Part 2a).

Nothing on an envelope says which store it came from. Two producer instances share a
schema, a pipeline_id and a seq range, so every measurement taken this way looks the
same whether it came from a development instance or from the series a release is
certified against. The producer answers the question on /v1/health and nowhere else.

Runs on its own thread rather than inside the transport's poll loop: its cadence is
half an hour against the transport's minute, and folding it in would make it inherit
the transport's back-off — a producer whose store is briefly unavailable would also
stop being asked who it is.
"""

import json
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.config_types.sentiment_config_types import SentimentHealthConfig
from python.framework.types.decision_logic_types import AwarenessLevel
from python.framework.types.signal_data_types import SignalHealthStatus

# What the producer calls a journal whose name its own mapping could not resolve. The
# id still binds — only the label is missing, which is not a fault worth alarming on.
UNRESOLVED_JOURNAL_NAME = 'unknown'


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
    ):
        """
        Initialize the health probe.

        Args:
            config: Probe cadence and timeout
            base_url: Address of the engine the transport consumes from
            logger: Session logger — the identity belongs in the run's artifacts, not
                only on screen, so a finished run can still say where its data came from
            api_token: Bearer token; empty means send no Authorization header
        """
        self._config = config
        self._logger = logger
        self._api_token = api_token
        self._on_event: Optional[Callable[[str, AwarenessLevel], None]] = None
        self._url = f"{base_url.rstrip('/')}/v1/health"

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._status = SignalHealthStatus()
        self._warned_unidentified = False

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
            )

    def probe_once(self) -> None:
        """Ask the producer who it is and record the answer."""
        payload = self._fetch()
        journal_id = payload.get('journal_id') or None
        journal_name = payload.get('environment') or UNRESOLVED_JOURNAL_NAME

        with self._lock:
            previous = self._status.journal_id
            changed = previous is not None and journal_id != previous
            self._status.journal_id = journal_id
            self._status.journal_name = journal_name if journal_id else ''
            self._status.engine_version = payload.get('version') or ''
            self._status.pass_timeout_s = payload.get('pass_timeout_seconds')
            self._status.probed_at = datetime.now(timezone.utc)
            if changed:
                self._status.journal_changed = True
            first_answer = previous is None and not changed

        if changed:
            self._report_change(previous, journal_id, journal_name)
        elif first_answer:
            self._report_first(journal_id, journal_name)

    def _run(self) -> None:
        """Probe until stopped, treating an unreachable producer as non-fatal."""
        while not self._stop.is_set():
            try:
                self.probe_once()
            except Exception as error:   # noqa: BLE001 — identity is never worth a crash
                with self._lock:
                    self._status.probe_errors += 1
                self._logger.debug(f"📡 Producer health probe failed: {error}")
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
        timeout_str = f"{timeout:.0f}s" if timeout is not None else 'unknown'
        self._emit(f"journal {journal_id} ({journal_name})", AwarenessLevel.INFO)
        self._logger.info(
            f"📡 Producer journal {journal_id} ({journal_name}) · engine {version} · "
            f"pass timeout {timeout_str}")

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
        current = f"{journal_id} ({journal_name})" if journal_id else 'unidentified'
        self._emit(f"journal CHANGED {previous} → {journal_id or 'none'}",
                   AwarenessLevel.ALERT)
        self._logger.error(
            f"📡 Producer journal changed mid-session: {previous} → {current}. The "
            f"sequence position accepted so far belongs to the previous journal and "
            f"does not carry over — measurements from this session span two series.")

    def _emit(self, message: str, level: AwarenessLevel) -> None:
        """
        Forward a transport moment to the tape, when one is attached.

        Args:
            message: Transport fact
            level: Display severity
        """
        if self._on_event is not None:
            self._on_event(message, level)
