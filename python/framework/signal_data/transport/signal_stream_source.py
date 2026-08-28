"""
FiniexTestingIDE - Signal Stream Source
The producer's push transport: one SSE connection per pipeline (#468).

Replaces the interim pull path, whose whole cost fell on the out-of-band passes — a
scheduled envelope seen 30 s late is meaningless against a ten-minute grid, a breaking one
seen 30 s late is 30 s of the move. Three properties are why this is a full-cadence stream
rather than a breaking-only channel: a breaking-only channel is edge-triggered INTO the
state and never reports the all-clear, a quiet one is indistinguishable from a frozen
producer, and with the cadence on the wire a silence longer than the producer's own
interval IS the staleness signal.

What it feeds does not change. It stamps a receipt time, parses with the same reader the
archive uses, and drops the result in the SignalInbox — ordering, deduplication and the
resolution gate stay the provider's, which is what lets a SIGNAL worker read a live series
and a mounted archive without being able to tell them apart.

Runs on its own thread because the loop must never wait on a socket.
"""

import json
import random
import socket
import threading
from collections import deque
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from typing import Deque, Optional, Set, Tuple, Type, TypeVar
from urllib.parse import quote, urlencode, urlparse

from pydantic import BaseModel, ValidationError

from python.framework.exceptions.signal_data_errors import (
    SignalStreamFrameTooLargeError,
    SignalStreamHttpError,
    SignalStreamSilenceError,
)
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.signal_data.transport.abstract_signal_transport import (
    AbstractSignalTransport,
)
from python.framework.signal_data.transport.signal_field_watch import SignalFieldWatch
from python.framework.signal_data.transport.signal_frame_recorder import SignalFrameRecorder
from python.framework.signal_data.producer.signal_health_probe import SignalHealthProbe
from python.framework.signal_data.producer.signal_http_reader import CREDENTIAL_STATUS_CODES
from python.framework.signal_data.transport.signal_inbox import SignalInbox
from python.framework.signal_data.signal_observed_accumulator import SignalObservedAccumulator
from python.framework.signal_data.transport.signal_sse_decoder import SignalSseDecoder
from python.framework.types.autotrader_types.autotrader_display_types import (
    SignalTransportEvent,
    SignalTransportStats,
)
from python.framework.types.config_types.sentiment_config_types import (
    ActiveProducer,
    SentimentStreamConfig,
)
from python.framework.types.decision_logic_types import AwarenessLevel
from python.framework.types.signal_data_types import (
    SUPPORTED_SCHEMA_MAJORS,
    ProducerStreamSettings,
    SignalHealthStatus,
    SignalSnapshot,
    SignalStreamControlCode,
    SignalStreamCursor,
    SignalStreamEventName,
    SignalStreamFrame,
    StreamControlFrame,
    StreamHeartbeatFrame,
    schema_major,
)

# Bytes taken per socket read. read1() returns what one underlying read produced rather
# than blocking for a full buffer, which is what keeps a frame's latency the frame's own.
READ_CHUNK_BYTES = 8192

# The three handles http.client splits ONE connection across. All three are kept because
# ownership moves: once an answer will close the connection, the connection no longer holds
# the socket, so closing it alone leaves a reader waiting on a socket nobody owns. The
# response half is None while a connect is still in flight.
OpenConnection = Tuple[HTTPConnection, Optional[HTTPResponse], Optional[socket.socket]]

# One frame model, for the shared validator below.
FrameModel = TypeVar('FrameModel', bound=BaseModel)

# How long the CONNECT itself may take — name resolution, the TCP handshake and, on https,
# the TLS handshake. Deliberately its own budget and not the watchdog: none of those phases
# has published a socket yet, so none of them can be shut down from another thread, and a
# stop landing inside one waits it out. Bounding it at the watchdog meant a session end
# could hold for a minute against an unreachable producer — measured 58 s at the served
# 20 s keep-alive — and in a live session that wait sits ahead of closing open positions.
CONNECT_TIMEOUT_S = 10.0

# The smallest watchdog the transport will use, whatever the producer serves. Guards the
# arithmetic rather than the producer: settimeout(0) puts the socket in NON-BLOCKING mode.
MINIMUM_WATCHDOG_S = 1.0

# How long a session end waits for the reader thread before abandoning it. Short on
# purpose: after the socket is shut down a blocked read returns at once, so the only phase
# that can outlast this is a connect nothing can interrupt — and a session end must not
# wait that out. In a live session `stop()` runs BEFORE open positions are closed, so a
# patient shutdown here is a patient shutdown in front of the thing that actually matters.
# The thread is a daemon; abandoning it costs a reported error, not a leak.
SHUTDOWN_JOIN_BUDGET_S = 2.0

# Statuses that mean the request itself was wrong, not the connection. Retrying a typo
# forever and calling it the producer's outage is the same misdiagnosis the credential
# rule exists to prevent — 404 is an unknown pipeline_id, 400 a parameter combination
# their contract refuses.
REQUEST_FAULT_STATUS_CODES = (400, 404)

# How many transport moments the operator panel keeps. The tail is what a human reads;
# the total is what tells them the tail is a tail.
TAPE_LENGTH = 8

# How many (stream_epoch, seq) identities are remembered for deduplication. A bounded
# replay is bounded by the producer's own window, which at their cadence is on the order
# of a hundred envelopes a day — this is ample, and bounded so a month-long session cannot
# grow a set forever.
SEEN_IDENTITY_LIMIT = 1024

# What the producer sends when its sequencer has no counter row for a stream yet. It is
# "not known", never generation zero — a distinction they shipped wrong once and caught in
# test, where it read 0 -> N as a rewind and closed every consumer attached to a newly
# added pipeline.
UNKNOWN_STREAM_EPOCH = 0


class SignalStreamSource(AbstractSignalTransport):
    """
    Holds one stream connection open and deposits arriving envelopes in the inbox.

    The cursor it tracks is the last CONTIGUOUS position, not the highest one seen. That
    distinction is the gap recovery: an envelope arriving past a hole is still enqueued —
    dropping a valid envelope helps nobody, and the provider deduplicates — but the cursor
    stays behind the hole so a reconnect asks the producer to fill it.
    """

    def __init__(
        self,
        config: SentimentStreamConfig,
        producer: ActiveProducer,
        stream_settings: ProducerStreamSettings,
        signal_kind: str,
        inbox: SignalInbox,
        logger: ScenarioLogger,
        cursor: Optional[SignalStreamCursor] = None,
        health_probe: Optional[SignalHealthProbe] = None,
        observed: Optional[SignalObservedAccumulator] = None,
        frame_recorder: Optional[SignalFrameRecorder] = None,
        connect_history: int = 1,
    ):
        """
        Initialize the stream source.

        Args:
            config: Stream transport configuration
            producer: Active endpoint with its resolved credential — address and token
                arrive as one unit so an environment switch cannot take effect by half
            stream_settings: The engine-wide values the producer serves, never configured
                here: the keep-alive interval the watchdog multiplies and the replay window
            signal_kind: Payload kind the snapshots are filed under
            inbox: Hand-off buffer drained by the loop
            logger: Session logger — operator-relevant failures belong here (§35)
            cursor: Position to resume from, from the boot bridge. None connects for the
                current snapshot instead, which is what a first session must do — the
                pre-stream archive carries no cursor
            health_probe: Optional producer-identity probe, started and stopped with this
                transport because it borrows this transport's address
            observed: Optional accumulator recording what the arriving envelopes state
                about themselves — the live half of the signal report
            frame_recorder: Optional recorder keeping the RAW frames beside their parsed
                form, for a certificate run. None in a trading session — the raw payload
                is otherwise dropped as soon as the snapshot is built, and a certificate
                cannot prove a field's ABSENCE, its wire TYPE or its LOCATION from a parsed
                object. Never a second source for a worker: nothing behind the inbox can
                reach it (§41)
            connect_history: How many envelopes to ask for on a CURSORLESS connect. One is
                the session's answer — the snapshot, and the stream carries the rest. A
                certificate run asks for more, because a single position cannot show that
                a series moved, and their contract offers `history=N` for exactly that.
                Ignored once a cursor exists: `history` and `since` are mutually exclusive
                and supplying both is a 400
        """
        self._config = config
        self._producer = producer
        self._settings = stream_settings
        self._signal_kind = signal_kind
        self._inbox = inbox
        self._logger = logger
        self._api_token = producer.credential.token
        self._health_probe = health_probe
        self._observed = observed
        self._frame_recorder = frame_recorder
        self._connect_history = max(1, connect_history)
        self._field_watch = SignalFieldWatch()
        self._route = (f"{producer.base_url.rstrip('/')}"
                       f'/v1/stream/{quote(config.pipeline_id, safe="")}')

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._open_parts: Optional[OpenConnection] = None
        # Set by a terminal condition — a revoked credential, a rewind somebody else
        # caused, a request the contract refuses. The loop exits instead of reconnecting.
        self._terminal = False
        # Set by a gap: leave the read loop and reconnect from the cursor at once, without
        # serving the backoff meant for a broken connection.
        self._refill_gap = False
        # Boundaries a replay has already been attempted for, so an unfillable hole is
        # reported once and then accepted rather than reconnected against forever.
        self._replayed_boundaries: Set[Tuple[int, int]] = set()

        # Identities already enqueued. A reconnect for a gap replays from the cursor, so
        # envelopes accepted PAST the hole arrive a second time — harmless for the series,
        # which deduplicates by the same key, but a second count in the observed
        # accumulator is a wrong number in the run report.
        self._seen: Deque[Tuple[int, int]] = deque(maxlen=SEEN_IDENTITY_LIMIT)
        self._seen_index: Set[Tuple[int, int]] = set()

        self._cursor = cursor
        # True between a connect that carried a cursor and the control frame that ends the
        # replay. Tracked rather than read off the state, because the state also carries
        # faults and an envelope arriving after one must not be read as "still replaying".
        self._replaying = False
        self._noted_retry_ms: Optional[int] = None
        self._connections = 0
        self._enqueued = 0
        self._replays = 0
        self._transport_errors = 0
        self._contract_errors = 0
        self._state = 'connecting'
        self._last_seq: Optional[int] = None
        self._last_epoch: Optional[int] = cursor.epoch if cursor else None
        self._last_envelope_at: Optional[datetime] = None
        self._tape: Deque[SignalTransportEvent] = deque(maxlen=TAPE_LENGTH)
        self._total_events = 0
        self._stats_lock = threading.Lock()
        if health_probe is not None:
            health_probe.set_event_sink(self._record)

    def start(self) -> None:
        """Open the connection on a background thread."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name='signal-stream', daemon=True)
        self._thread.start()
        if self._health_probe is not None:
            self._health_probe.start()
        resume = self._cursor.describe() if self._cursor else 'no cursor — current snapshot'
        self._record(f'connecting — {resume}')
        self._logger.info(f'📡 Signal stream starting: {self._route} ({resume})')

    def stop(self) -> None:
        """Close the connection and wait for the thread to finish."""
        self._stop.set()
        self._close_open()
        if self._health_probe is not None:
            self._health_probe.stop()
        stranded = False
        if self._thread is not None:
            self._thread.join(timeout=SHUTDOWN_JOIN_BUDGET_S)
            stranded = self._thread.is_alive()
            self._thread = None
        self._logger.info(
            f'📡 Signal stream stopped: {self._connections} connections, '
            f'{self._enqueued} envelopes, {self._replays} replays requested')
        if stranded:
            # Abandoned deliberately, and REPORTED rather than assumed away. Waiting longer
            # would hold the session end — which in a live run sits in front of closing open
            # positions — for a connect that cannot be interrupted. So the thread is left to
            # die with the process and the operator is told, because it can still deposit
            # into an inbox nobody drains and still write to a logger whose buffer the
            # summary has already collected (§35). Saying "stopped" alone would be a claim
            # this code did not establish.
            self._logger.error(
                f'📡 The signal stream thread did not finish within '
                f'{SHUTDOWN_JOIN_BUDGET_S:.0f}s and was ABANDONED — almost certainly inside '
                f'a connect, which nothing can interrupt. The session end continues rather '
                f'than waiting it out. The thread dies with the process; anything it logs '
                f'from here on arrives after this session summary was collected.')

    def get_stats(self) -> Tuple[int, int, int]:
        """
        Session counters.

        Returns:
            (connections opened, envelopes enqueued, replays requested)
        """
        with self._stats_lock:
            return self._connections, self._enqueued, self._replays

    def get_cursor(self) -> Optional[SignalStreamCursor]:
        """
        The last contiguous position this session accepted.

        Returns:
            The cursor a reconnect would resume from, or None when nothing has been
            accepted and no cursor was supplied
        """
        with self._stats_lock:
            return self._cursor

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
                transport_errors=self._transport_errors,
                contract_errors=self._contract_errors,
                tape=list(self._tape),
                total_events=self._total_events,
                health=health,
            )

    def _watchdog_timeout_s(self) -> float:
        """
        How long a silent socket may stay silent before it counts as broken.

        The producer's served keep-alive interval times our multiple. This is a CONNECTION
        watchdog and never a freshness claim: the keep-alive proves the socket is alive, a
        stalled seq proves the producer is not, and only the second is the provider's
        business.

        Returns:
            The timeout in seconds
        """
        # Floored: settimeout(0) means NON-BLOCKING, not "no timeout", so a producer that
        # served a zero interval would turn the read loop into a hot spin rather than a
        # patient one. A floor is the honest reading of a nonsensical value.
        return max(self._settings.heartbeat_seconds
                   * self._config.heartbeat_timeout_multiple,
                   MINIMUM_WATCHDOG_S)

    def _connect_timeout_s(self) -> float:
        """
        How long the uninterruptible connect phase may take.

        Never longer than the watchdog: waiting longer to REACH a producer than we would
        wait for one that has gone silent makes no sense, and it is what keeps a test-scale
        configuration from inheriting a ten-second budget.

        Returns:
            The connect budget in seconds
        """
        return min(CONNECT_TIMEOUT_S, self._watchdog_timeout_s())

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
        """
        Hold a connection open, reconnecting until stopped or a terminal frame.

        A terminal frame also silences the identity probe. It borrows this transport's
        address to answer which journal the envelopes came from — and once there are no
        envelopes, asking every half hour for the rest of the session is a live component
        attached to a dead feed. What it already learned is kept; only the asking stops.
        """
        try:
            self._reconnect_until_done()
        finally:
            if self._terminal:
                self._silence_health_probe()

    def _silence_health_probe(self) -> None:
        """Stop asking the producer who it is, once the feed is over for this session."""
        if self._health_probe is None:
            return
        self._health_probe.stop()
        self._record('identity probe stopped — the feed is over', AwarenessLevel.NOTICE)

    def _reconnect_until_done(self) -> None:
        """Reconnect until the session stops or a terminal frame ends the feed."""
        backoff_s = self._config.reconnect_backoff_initial_s
        while not self._stop.is_set() and not self._terminal:
            try:
                self._connect_once()
                if self._terminal or self._stop.is_set():
                    return
                # A clean close is the producer's business, not a fault — reconnect at the
                # base delay rather than escalating a backoff against a healthy server.
                backoff_s = self._config.reconnect_backoff_initial_s
                if self._refill_gap:
                    self._refill_gap = False
                    continue
            except Exception as error:   # noqa: BLE001 — a transport fault never ends the session
                # A socket we closed ourselves during shutdown is not a failure, and
                # reporting it as one puts a phantom outage in the session's error pot.
                if self._stop.is_set():
                    return
                self._record_transport_failure(error)
            self._stop.wait(self._jittered(backoff_s))
            backoff_s = min(backoff_s * 2.0, self._config.reconnect_backoff_max_s)

    @staticmethod
    def _jittered(delay_s: float) -> float:
        """
        Spread a reconnect delay so simultaneous clients do not return in lockstep.

        Args:
            delay_s: The delay the backoff arrived at

        Returns:
            A delay between half and the whole of it
        """
        return delay_s * random.uniform(0.5, 1.0)

    def _connect_once(self) -> None:
        """Open one connection and read it until it ends, is stopped, or turns terminal."""
        decoder = SignalSseDecoder()
        self._replaying = self._cursor is not None
        with self._stats_lock:
            self._connections += 1
            self._state = 'replay' if self._replaying else 'connecting'

        try:
            response = self._open()
            if response is None:
                return
            self._read(response, decoder)
        finally:
            # Covers the open as well as the read: a request that raises after the handle
            # was published would otherwise leave a live socket behind on every retry.
            self._close_open()

    def _open(self) -> Optional[HTTPResponse]:
        """
        Connect, send the request, and classify the answer.

        Built on http.client rather than urllib because the SOCKET has to be reachable:
        a session end must be able to shut it down, and urllib hands out no handle. The
        socket keeps ONE timeout — the watchdog — for connect, response head and every
        frame read alike. A shorter polling timeout was tried and is a trap: CPython's
        SocketIO marks a file object permanently timed out after its first expiry, so the
        second read raises a plain OSError rather than TimeoutError and a healthy
        connection is torn down one poll after the last frame.

        The handle is published before the response is read, so a stop during a connect
        that never answers has something to shut down. The two ways this ends without a
        response are deliberately different: a REFUSAL is handled here and returns None,
        because the transport is finished and there is nothing to retry. Anything else is
        raised, because backing off and reconnecting is exactly the right answer to it.

        Returns:
            The open response, or None when the request was refused for good
        """
        parsed = urlparse(self._build_url())
        factory = HTTPSConnection if parsed.scheme == 'https' else HTTPConnection
        connection = factory(parsed.hostname, parsed.port,
                             timeout=self._connect_timeout_s())
        headers = {'Accept': 'text/event-stream'}
        if self._api_token:
            headers['Authorization'] = f'Bearer {self._api_token}'

        # The connect runs on its own short budget, because it is the one phase nothing can
        # interrupt — no socket exists to shut down until it returns.
        connection.connect()

        # From here on there IS a socket, so a stop takes effect at once and the watchdog
        # can safely be the long one. Published before the response head is read: a
        # producer that accepts the connection and then never answers would otherwise
        # leave stop() with nothing to interrupt.
        sock = connection.sock
        self._open_parts = (connection, None, sock)
        sock.settimeout(self._watchdog_timeout_s())

        target = parsed.path + (f'?{parsed.query}' if parsed.query else '')
        connection.request('GET', target, headers=headers)
        response = connection.getresponse()
        # Re-published with the response: http.client hands the socket over and clears its
        # own reference whenever the answer will close the connection, so `sock` above
        # stays the handle a stop needs.
        self._open_parts = (connection, response, sock)

        if response.status == 200:
            return response

        status, reason = response.status, response.reason
        self._close_open()
        if status in CREDENTIAL_STATUS_CODES:
            self._handle_unauthorized(status)
            return None
        if status in REQUEST_FAULT_STATUS_CODES:
            self._handle_request_fault(status, reason)
            return None
        raise SignalStreamHttpError(f'HTTP {status} — {reason}')

    def _read(self, response: HTTPResponse, decoder: SignalSseDecoder) -> None:
        """
        Read frames until the connection ends, the session stops, or a frame is terminal.

        The socket's own timeout IS the connection watchdog, so silence past the keep-alive
        the producer promised arrives here as a TimeoutError rather than needing a second
        thread to notice it. A stop does not wait for that: it shuts the socket down, which
        makes a blocked receive return at once — closing alone would not.

        Args:
            response: The open response
            decoder: The decoder accumulating this connection's frames
        """
        while not self._stop.is_set() and not self._terminal:
            try:
                chunk = response.read1(READ_CHUNK_BYTES)
            except TimeoutError as error:
                raise SignalStreamSilenceError(
                    f'no data for {self._watchdog_timeout_s():.0f}s — the socket is silent '
                    f'past the keep-alive the producer promised') from error
            if not chunk:
                # A shutdown we initiated ends the read the same way the producer hanging
                # up does. Saying "producer closed the connection" at every session end
                # blames them for our own stop, on the tape an operator reads afterwards.
                if not self._stop.is_set():
                    self._record('producer closed the connection', AwarenessLevel.NOTICE)
                return
            self._note_suggested_retry(decoder)
            try:
                frames = decoder.feed(chunk)
            except SignalStreamFrameTooLargeError as error:
                # Their answer, our inability to hold it — a contract violation, not a
                # transport fault. The connection is abandoned because the framing is what
                # broke: there is no point reading further bytes of a stream whose line
                # boundaries we can no longer find.
                self._report_contract_violation('stream framing', str(error))
                return
            for frame in frames:
                self._handle_frame(frame)
                if self._terminal or self._refill_gap:
                    return

    def _note_suggested_retry(self, decoder: SignalSseDecoder) -> None:
        """
        Report the producer's in-band reconnect suggestion, once, without obeying it.

        Settled cross-repo as a default for a client with no policy of its own — ours
        governs. Worth showing anyway: a suggestion that moves is a signal about their
        side, and it is the only place they express one.

        Args:
            decoder: The decoder that may have seen a `retry:` line
        """
        suggested = decoder.get_retry_ms()
        if suggested is None or self._noted_retry_ms == suggested:
            return
        self._noted_retry_ms = suggested
        self._record(f'producer suggests {suggested} ms retry · ours governs',
                     AwarenessLevel.NOTICE)

    def _build_url(self) -> str:
        """
        The connect URL for this attempt.

        `since` and `epoch` travel together or not at all — the producer answers 400 to
        either half alone, because a seq belongs to an epoch and serving `since+1..` of a
        series we may not be on is worse than refusing. `history` is mutually exclusive
        with them, so a cursor suppresses it.

        Returns:
            The full URL
        """
        cursor = self.get_cursor()
        if cursor is not None:
            query = urlencode({'since': cursor.seq, 'epoch': cursor.epoch})
        else:
            query = urlencode({'history': self._connect_history})
        return f'{self._route}?{query}'

    def _close_open(self) -> None:
        """Close the current connection — what makes a stop take effect at once."""
        parts, self._open_parts = self._open_parts, None
        self._close_parts(parts)

    @staticmethod
    def _close_parts(parts: Optional[OpenConnection]) -> None:
        """
        Shut the socket down, then close all three handles.

        The shutdown comes first and is the part that matters: closing a socket does NOT
        interrupt a receive already blocked on it, so a session end would wait out the read
        timeout on a connection it had already abandoned. A shutdown makes the blocked read
        return at once.

        All three handles because http.client splits ownership — once an answer will close
        the connection, the connection no longer holds the socket, and closing it alone
        leaves a reader waiting on a socket nobody owns any more.

        Args:
            parts: The (connection, response, socket) triple, or None
        """
        if parts is None:
            return
        connection, response, sock = parts
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass   # already closed by the far end, which is the ordinary case
        for part in (connection, response, sock):
            if part is None:
                continue   # the response half is absent while a connect is still in flight
            try:
                part.close()
            except Exception:   # noqa: BLE001 — closing a dead socket is not a failure
                pass

    def _handle_frame(self, frame: SignalStreamFrame) -> None:
        """
        Route one decoded frame to its handler.

        Args:
            frame: The dispatched frame
        """
        if frame.event == SignalStreamEventName.SIGNAL.value:
            self._handle_signal(frame.data)
        elif frame.event == SignalStreamEventName.HEARTBEAT.value:
            self._handle_heartbeat(frame.data)
        elif frame.event == SignalStreamEventName.CONTROL.value:
            self._handle_control(frame.data)
        else:
            # Contract growth, not a fault: they name every event, so an unknown name
            # means the shape grew. Reported once it is seen, never guessed at.
            self._record(f"unknown frame '{frame.event}'", AwarenessLevel.NOTICE)
            self._logger.warning(
                f"📡 Producer stream sent an event we do not handle: '{frame.event}'. "
                f'Not an error — the shape grew and we ignore the new parts.')

    def _handle_signal(self, data: str) -> None:
        """
        Parse one envelope frame and enqueue it.

        Args:
            data: The frame's payload
        """
        payload = self._decode_payload(data, 'signal')
        if payload is None:
            return

        received = datetime.now(timezone.utc)
        try:
            snapshot = SignalSnapshot.model_validate(
                {**payload, 'collected_msc': int(received.timestamp() * 1000)})
        except ValidationError as error:
            first = (error.errors() or [{}])[0]
            location = '.'.join(str(part) for part in first.get('loc', ())) or 'envelope'
            self._report_contract_violation(
                location, str(first.get('msg', 'validation failed')))
            return

        # The archive path has always gated on the major; a live path that did not would
        # mis-read a breaking bump rather than refuse it. Their MINOR is additive by rule,
        # so a minor we have not seen is readable by construction and passes here.
        if schema_major(snapshot.schema_version) not in SUPPORTED_SCHEMA_MAJORS:
            supported = ', '.join(f'{m}.x' for m in sorted(SUPPORTED_SCHEMA_MAJORS))
            self._report_contract_violation(
                'schema_version',
                f'{snapshot.schema_version} is a major this reader does not support '
                f'(supports {supported})')
            return

        self._announce_unread_fields(payload, snapshot.schema_version)
        if self._frame_recorder is not None:
            # Recorded BEFORE the duplicate gate: a certificate asks what the wire
            # delivered, and a redelivery is something the wire did.
            self._frame_recorder.record(
                envelope=payload, snapshot=snapshot, received=received,
                frame_bytes=len(data.encode('utf-8')))
        if self._is_duplicate(snapshot):
            self._advance_cursor(snapshot)
            return
        self._enqueue(snapshot)
        self._advance_cursor(snapshot)

    def _is_duplicate(self, snapshot: SignalSnapshot) -> bool:
        """
        Whether this envelope has already been enqueued this session.

        Args:
            snapshot: The parsed envelope

        Returns:
            True when its (stream_epoch, seq) was seen before — an envelope carrying
            neither is never treated as a duplicate, because it has no identity to compare
        """
        if snapshot.seq is None or snapshot.stream_epoch is None:
            return False
        identity = (snapshot.stream_epoch, snapshot.seq)
        if identity in self._seen_index:
            return True
        if len(self._seen) == self._seen.maxlen:
            self._seen_index.discard(self._seen[0])
        self._seen.append(identity)
        self._seen_index.add(identity)
        return False

    def _enqueue(self, snapshot: SignalSnapshot) -> None:
        """
        Hand one envelope to the inbox and record it for the operator.

        Enqueued even when it sits past a gap: a valid envelope withheld helps nobody, and
        the provider deduplicates by (stream_epoch, seq), so a replay delivering it a
        second time changes nothing.

        Args:
            snapshot: The parsed envelope
        """
        self._inbox.put(self._signal_kind, [snapshot])
        if self._observed is not None:
            self._observed.observe(snapshot)
        with self._stats_lock:
            self._enqueued += 1
            # An accepted envelope means the transport is working, so it also CLEARS a
            # previous fault state — a contract error would otherwise sit on the panel
            # until the next reconnect, a healthy feed reading as a broken one.
            self._state = 'replay' if self._replaying else 'live'
            self._last_seq = snapshot.seq
            self._last_epoch = snapshot.stream_epoch
            self._last_envelope_at = snapshot.get_resolution_key()
        # "<trigger> pass", never the bare word: 'breaking' here is why the PASS ran, not
        # the model's verdict for a symbol. The two are independent — an out-of-band pass
        # can carry is_breaking=False for the symbol we trade.
        trigger = snapshot.trigger_reason or 'unknown-trigger'
        self._record(
            f"seq {snapshot.seq if snapshot.seq is not None else '—'} · {trigger} pass")

    def _advance_cursor(self, snapshot: SignalSnapshot) -> None:
        """
        Move the contiguous cursor, or notice the hole that stops it moving.

        Args:
            snapshot: The envelope just enqueued
        """
        if snapshot.seq is None or snapshot.stream_epoch is None:
            return
        cursor = self.get_cursor()

        # A newer generation always takes over: a seq is only comparable inside its own
        # epoch, so there is nothing to be contiguous with across a reset.
        if cursor is None or snapshot.stream_epoch > cursor.epoch:
            self._set_cursor(snapshot.stream_epoch, snapshot.seq)
            return
        # An older generation cannot move a newer cursor — and comparing its seq against
        # one from a later epoch would be comparing two different countings.
        if snapshot.stream_epoch < cursor.epoch:
            return

        expected = cursor.seq + 1
        if snapshot.seq > expected:
            self._handle_gap(cursor, snapshot.seq)
            return
        if snapshot.seq >= expected:
            self._set_cursor(snapshot.stream_epoch, snapshot.seq)

    def _set_cursor(self, epoch: int, seq: int) -> None:
        """
        Move the contiguous cursor, unless the epoch is the producer's "not known yet".

        `stream_epoch: 0` means the sequencer holds no counter row for this stream — it is
        UNKNOWN, never generation zero. Adopting it would put `?epoch=0` on the next
        reconnect, a position that describes no series. The rule is the producer's own,
        stated after they shipped and caught the mirror image of it: reading 0 → N as a
        series change closed every consumer attached to a newly added pipeline with a false
        rewind. We wait for a real epoch instead.

        Args:
            epoch: Stream epoch the position belongs to
            seq: Sequence number reached
        """
        if epoch <= UNKNOWN_STREAM_EPOCH:
            self._record('epoch not known yet — no cursor taken', AwarenessLevel.NOTICE)
            return
        with self._stats_lock:
            self._cursor = SignalStreamCursor(epoch=epoch, seq=seq)

    def _handle_gap(self, cursor: SignalStreamCursor, arrived_seq: int) -> None:
        """
        Ask for a bounded replay of a hole — once per hole.

        The second encounter of the same boundary means the producer cannot fill it, so
        the cursor moves past it instead: reconnecting forever against an unfillable hole
        turns a reported gap into an outage. What a hole costs is that the series does not
        advance across it, which the staleness contract already describes.

        Args:
            cursor: The contiguous position the hole starts after
            arrived_seq: The sequence number that arrived instead
        """
        boundary = (cursor.epoch, cursor.seq)
        missing = arrived_seq - cursor.seq - 1
        if boundary in self._replayed_boundaries:
            with self._stats_lock:
                self._cursor = SignalStreamCursor(epoch=cursor.epoch, seq=arrived_seq)
            self._record(f'gap of {missing} accepted after {cursor.describe()}',
                         AwarenessLevel.NOTICE)
            self._logger.warning(
                f'📡 Signal stream gap after {cursor.describe()}: {missing} envelope(s) '
                f'could not be replayed and the series resumes at seq {arrived_seq}. The '
                f'staleness contract covers the hole.')
            return

        self._replayed_boundaries.add(boundary)
        with self._stats_lock:
            self._replays += 1
        self._refill_gap = True
        self._record(f'gap of {missing} after {cursor.describe()} — replaying',
                     AwarenessLevel.NOTICE)
        self._logger.warning(
            f'📡 Signal stream gap after {cursor.describe()}: seq {arrived_seq} arrived, '
            f'{missing} missing. Reconnecting for a bounded replay.')

    def _handle_heartbeat(self, data: str) -> None:
        """
        Record one keep-alive — proof of the SOCKET, never of freshness.

        Args:
            data: The frame's payload
        """
        payload = self._decode_payload(data, 'heartbeat')
        if payload is None:
            return
        beat = self._parse_frame(StreamHeartbeatFrame, payload, 'heartbeat')
        if beat is None:
            return
        position = beat.seq if beat.seq is not None else '—'
        skew = ''
        if beat.now_msc is not None:
            local_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            skew = f' · skew {local_ms - beat.now_msc:+d} ms'
        self._record(f'keep-alive · seq {position}{skew}')

    def _handle_control(self, data: str) -> None:
        """
        Route one control frame — the two rewind diagnoses to different responses.

        Args:
            data: The frame's payload
        """
        payload = self._decode_payload(data, 'control')
        if payload is None:
            return
        control = self._parse_frame(StreamControlFrame, payload, 'control')
        if control is None:
            return
        code = control.resolve_code()

        if code is SignalStreamControlCode.LIVE:
            self._handle_live(control)
        elif code is SignalStreamControlCode.REPLAY_TRUNCATED:
            self._handle_replay_truncated(control)
        elif code is SignalStreamControlCode.EPOCH_CHANGED:
            self._handle_epoch_changed(control)
        elif code is SignalStreamControlCode.CURSOR_AHEAD:
            self._handle_cursor_ahead(control)
        elif code is SignalStreamControlCode.AUTH_REVOKED:
            self._handle_auth_revoked(control)
        else:
            self._record(f"unknown control '{control.code}'", AwarenessLevel.NOTICE)
            self._logger.warning(
                f"📡 Producer stream sent control code '{control.code}', which this "
                f'reader does not know. Not an error — their vocabulary grew.')

    def _handle_live(self, control: StreamControlFrame) -> None:
        """
        Replay or snapshot is done; everything after this frame is live.

        Args:
            control: The decoded frame
        """
        self._replaying = False
        with self._stats_lock:
            self._state = 'live'
        head = control.head_seq
        if head == 0:
            self._record('live · producer has never published')
            self._logger.info(
                '📡 Signal stream live: this pipeline has produced no envelope yet '
                '(head_seq 0). The producer is reachable; there is simply nothing to read.')
            return
        self._record(f'live · head_seq {head}')
        self._logger.info(f'📡 Signal stream live at head_seq {head}')

    def _handle_replay_truncated(self, control: StreamControlFrame) -> None:
        """
        Our cursor was older than the producer's replay window — accept the hole.

        The cursor jumps to just before the oldest envelope they still hold, so the first
        arrival is contiguous. Without that jump the hole would look like a gap and start
        a replay for envelopes the producer has already said it does not have.

        Args:
            control: The decoded frame
        """
        oldest = control.oldest_available_seq
        cursor = self.get_cursor()
        lost = (oldest - cursor.seq - 1) if (oldest is not None and cursor) else None
        if oldest is not None and control.stream_epoch is not None:
            with self._stats_lock:
                self._cursor = SignalStreamCursor(
                    epoch=control.stream_epoch, seq=oldest - 1)
        self._record(f'replay truncated at seq {oldest}', AwarenessLevel.NOTICE)
        self._logger.warning(
            f'📡 Signal replay truncated: asked since={control.requested_since}, oldest '
            f'available {oldest} (window {control.window_hours} h). '
            f"{lost if lost is not None else 'Some'} envelope(s) are unrecoverable — the "
            f'series resumes at {oldest} and the staleness contract covers the hole.')

    def _handle_epoch_changed(self, control: StreamControlFrame) -> None:
        """
        The PRODUCER rewound. Reconnect through the connect path with the new epoch.

        A terminal frame on both paths — they emit it and close — which is exactly why
        there is no second resync path here: the reconnect the loop performs anyway does
        the work. Their old sequence numbers mean nothing in the new epoch, so the cursor
        becomes the new epoch's head; the snapshots already delivered keep their own epoch
        and stay ordered before it.

        Args:
            control: The decoded frame
        """
        if (control.stream_epoch == UNKNOWN_STREAM_EPOCH
                or control.previous_epoch == UNKNOWN_STREAM_EPOCH):
            # Not a rewind: one side of the comparison is "not known yet". Alerting here
            # would report a series change to the operator that never happened.
            self._record('epoch announced from an unknown generation — adopting',
                         AwarenessLevel.NOTICE)
            self._logger.info(
                f'📡 Signal stream reported epoch {control.previous_epoch} → '
                f'{control.stream_epoch}, one of which is the producer\'s "not known yet". '
                f'Read as a cold sequencer rather than a rewind; the first real epoch is '
                f'adopted on arrival.')
            self._replayed_boundaries.clear()
            return

        if control.stream_epoch is not None and control.head_seq is not None:
            # One BEFORE the head, not the head itself. `since=N` serves N+1 onward — the
            # same reading every other cursor here rests on — so resuming AT head_seq would
            # skip the newest envelope of the epoch we just moved to, once per rewind and
            # silently. head_seq 0 means the new epoch is empty; since=0 then asks for its
            # first envelope, because their counter starts at 1.
            self._set_cursor(control.stream_epoch, max(control.head_seq - 1, 0))
        self._replayed_boundaries.clear()
        self._record(
            f'epoch changed {control.previous_epoch} → {control.stream_epoch}',
            AwarenessLevel.ALERT)
        self._logger.warning(
            f'📡 Signal stream epoch changed: {control.previous_epoch} → '
            f'{control.stream_epoch} — the PRODUCER rewound (restore, PITR or promotion). '
            f'Reconnecting at head_seq {control.head_seq}. Envelopes already received keep '
            f'the previous epoch and stay ordered before the new one.')

    def _handle_cursor_ahead(self, control: StreamControlFrame) -> None:
        """
        SOMEBODY ELSE rewound — most likely our own store was restored. Stop and alert.

        Deliberately not the same response as an epoch change, and the difference is the
        whole reason the producer emits two codes: there, they rewound and reconnecting is
        correct; here, we are ahead of them and silently resuming would paper over a
        restored consumer store. A human decides.

        Terminal, confirmed from their wire rather than from intent: the server closes
        after the frame, on connect AND mid-stream. Their rule, worth carrying because it
        settles the whole family in one sentence — a control code that says YOUR CURSOR IS
        UNUSABLE is terminal. `replay_truncated` is not one: it says the cursor is older
        than what will be replayed, which is recoverable, so the connection continues.

        Args:
            control: The decoded frame
        """
        self._terminal = True
        with self._stats_lock:
            self._state = 'cursor_ahead'
        self._record(f'cursor ahead of producer head {control.head_seq}',
                     AwarenessLevel.ALERT)
        self._logger.error(
            f'📡 Signal stream STOPPED — our cursor (seq {control.requested_since}) is '
            f'AHEAD of the producer head (seq {control.head_seq}). This is NOT their '
            f'rewind: most likely our own store was restored from a backup. Resuming '
            f'silently would hide that, so the feed stops here and the staleness contract '
            f'declares it blind. Check the local signal archive, then restart.')

    def _handle_auth_revoked(self, control: StreamControlFrame) -> None:
        """
        The producer revoked our token mid-stream. Stop — retrying cannot fix it.

        Not reachable on their side yet: their token registry is loaded at boot, so a
        revocation today means a restart, and a restart closes every connection anyway.
        The handler stays because the code is specified and the config-reload work that
        makes it fire is scheduled — until then a dead credential arrives as the 401 on
        reconnect, which lands on the same treatment.

        Args:
            control: The decoded frame
        """
        detail = control.detail or 'no detail given'
        self._terminal = True
        with self._stats_lock:
            self._state = 'unauthorized'
        self._record('credential revoked mid-stream', AwarenessLevel.ALERT)
        self._logger.error(
            f'📡 Producer revoked our credential mid-stream ({detail}) — this is NOT a '
            f'producer outage. The stream stopped; retrying cannot fix a token. Endpoint '
            f'{self._producer.name}, credential '
            f'{self._producer.credential.describe_source()} — fix it and restart the '
            f'session. The feed is now blind and the staleness contract will say so.')

    def _handle_unauthorized(self, status_code: int) -> None:
        """
        Report a credential problem on connect and stop — retrying cannot fix it.

        Deliberately not counted as a transport error: the producer is answering
        correctly, we are the ones without a valid token. Stopping is safe because the
        staleness contract then declares the feed blind, which is a state the decision
        logic is required to handle — a silence the strategy is TOLD about rather than one
        it has to infer.

        Args:
            status_code: The status the producer answered with
        """
        self._terminal = True
        with self._stats_lock:
            self._state = 'unauthorized'
        self._record(f'credential rejected ({status_code})', AwarenessLevel.ALERT)
        self._logger.error(
            f'📡 Producer rejected our credential ({status_code}) — this is NOT a producer '
            f'outage. The stream stopped; retrying cannot fix a token. Endpoint '
            f'{self._producer.name}, credential '
            f'{self._producer.credential.describe_source()} — fix it and restart the '
            f'session. The feed is now blind and the staleness contract will say so.')

    def _handle_request_fault(self, status_code: int, reason: str) -> None:
        """
        The producer refused the REQUEST — a typo or a parameter combination, not an outage.

        Stopping rather than retrying is the point. A 404 is an unknown pipeline_id, and a
        client that cannot tell "exists but idle" from "does not exist" waits forever on a
        misspelled name while the panel shows a healthy-looking reconnect loop.

        Args:
            status_code: The status the producer answered with
            reason: The reason line it came with
        """
        self._terminal = True
        with self._stats_lock:
            self._state = 'misconfigured'
        self._record(f'request refused ({status_code})', AwarenessLevel.ALERT)
        named = ('the pipeline id is not registered with this producer'
                 if status_code == 404
                 else 'the producer refused the parameters we sent')
        self._logger.error(
            f'📡 Producer refused the stream request ({status_code} {reason}) — {named}. '
            f'Requested: {self._build_url()}. This is NOT an outage and retrying cannot '
            f'fix it; the stream stopped. Check stream.pipeline_id in sentiment_config.json '
            f'against GET /v1/pipelines, then restart the session.')

    def _parse_frame(self, model: Type[FrameModel], payload: dict,
                     event: str) -> Optional[FrameModel]:
        """
        Validate one non-envelope frame, reporting a shape we cannot read as OUR problem.

        The signal path has always done this; control and heartbeat did not, and the
        asymmetry was the bug: an unguarded ValidationError escaped the frame handler, was
        caught by the reconnect loop's blanket handler and counted as a TRANSPORT fault. A
        producer that renamed one control field would then have presented as their outage,
        in an endless reconnect storm — precisely the misattribution the credential rule and
        the contract-error split exist to prevent.

        Args:
            model: The Pydantic model for this frame kind
            payload: The decoded object
            event: Event name, for the operator message

        Returns:
            The validated frame, or None when it could not be read
        """
        try:
            return model.model_validate(payload)
        except ValidationError as error:
            first = (error.errors() or [{}])[0]
            location = '.'.join(str(part) for part in first.get('loc', ())) or event
            self._report_contract_violation(
                f'{event} frame · {location}',
                str(first.get('msg', 'validation failed')))
            return None

    def _decode_payload(self, data: str, event: str) -> Optional[dict]:
        """
        Decode one frame's JSON, reporting a frame we cannot read as a contract error.

        Args:
            data: The frame's payload
            event: Event name, for the operator message

        Returns:
            The decoded object, or None when it could not be read
        """
        try:
            payload = json.loads(data)
        except ValueError as error:
            self._report_contract_violation(f'{event} frame', f'undecodable JSON: {error}')
            return None
        if not isinstance(payload, dict):
            self._report_contract_violation(
                f'{event} frame', f'expected an object, got {type(payload).__name__}')
            return None
        return payload

    def _announce_unread_fields(self, payload: dict, version: str) -> None:
        """
        Name the fields the producer sent that we do not read — once per distinct set.

        Args:
            payload: The envelope as it arrived
            version: The schema version it declared
        """
        unread = self._field_watch.take_new(payload)
        if not unread:
            return
        named = ', '.join(unread)
        self._record(f'unread fields: {named}', AwarenessLevel.NOTICE)
        self._logger.warning(
            f'📡 Producer envelope (schema {version}) carries fields we do not read: '
            f'{named}. Not an error — the shape grew and we ignore the new parts. Read it '
            f'as a prompt to decide whether they belong in our contract.')

    def _record_transport_failure(self, error: Exception) -> None:
        """
        Count and report a genuine transport fault, which never ends the session.

        Args:
            error: The failure to report
        """
        with self._stats_lock:
            self._transport_errors += 1
            self._state = 'error'
        self._record(f'connection failed: {type(error).__name__}', AwarenessLevel.ALERT)
        self._logger.warning(f'📡 Signal stream failed: {error}')

    def _report_contract_violation(self, location: str, detail: str) -> None:
        """
        Report a frame we cannot read — and never call it a transport fault.

        The producer answered; our reader could not parse what it said. Counted as a
        transport error this blames their infrastructure for our schema being out of date,
        and it is silent: the connection would be dropped and retried against a mismatch
        retrying cannot fix, while the staleness contract declares the feed blind for the
        wrong reason.

        The connection CONTINUES, unlike the credential case: one malformed frame must not
        end a session, and a producer-side fix should be picked up without a restart. What
        changes is that the operator is told which field disagreed, and the error enters
        the session pot — so the run grades FINISHED_WITH_ERRORS (#372).

        Args:
            location: Field path that disagreed, as the operator would look it up
            detail: What was wrong with it
        """
        with self._stats_lock:
            self._contract_errors += 1
            self._state = 'contract'
        self._record(f'frame unreadable: {location}', AwarenessLevel.ALERT)
        self._logger.error(
            f'📡 Producer frame failed OUR schema at `{location}`: {detail}. '
            f'This is NOT a producer outage — they answered, we could not read it. '
            f'The connection stays open, but nothing is reaching the decision path until '
            f'it is fixed.')
