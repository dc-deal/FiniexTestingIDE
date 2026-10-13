"""
FiniexTestingIDE - Operator Alert Dispatcher

Carries operator alerts to their channel on a thread of its own (#235), so the tick loop never
waits on a network: `send()` is a queue append under a lock and returns at once.

The thread paces the messages (`min_send_interval_s` — Telegram allows about one per second to a
chat), honours a channel's request to wait (`retry_after`), and retries one message through the
ladder of §43; the thread owns the wait. A message the ladder gives up on is dropped and the next
one is tried. A TERMINAL answer — a refused token, an unknown chat — closes the channel for the
session instead, because every later message would be refused the same way. The ladder's give-up
line lands in the session log, so a run whose alerts never arrived is graded with errors rather
than looking clean.
"""

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, Optional

from python.framework.autotrader.operator_alerts.abstract_alert_channel import (
    AbstractAlertChannel,
)
from python.framework.autotrader.operator_alerts.operator_alert_messages import render_alert_text
from python.framework.exceptions.operator_alert_errors import AlertChannelRateLimitedError
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlert,
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.operator_alerts_config_types import TelegramAlertsConfig
from python.framework.types.connection_types import ConnectionOutcome
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder


class OperatorAlertDispatcher:
    """
    The queue and the thread between the session and its alert channel.

    Args:
        channel: Where the messages go
        config: The channel's pacing, queue size and retry ladder
        logger: The session logger — a give-up belongs in the §35 error pot
        session_label: Who is speaking, on every message — several bots may share one chat
        clock: Monotonic clock for the pacing — injected by tests
        sleep: How to wait between messages and retries — injected by tests
    """

    def __init__(
        self,
        channel: AbstractAlertChannel,
        config: TelegramAlertsConfig,
        logger: AbstractLogger,
        session_label: str,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ):
        self._channel = channel
        self._min_interval_s = config.min_send_interval_s
        self._capacity = config.queue_capacity
        self._logger = logger
        self._session_label = session_label
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep
        self._ladder = ConnectionLadder('operator_alerts', config.connection, logger)
        self._queue: Deque[OperatorAlert] = deque()
        self._condition = threading.Condition()
        self._stopping = False
        self._in_flight = False
        self._closed = False
        self._thread: Optional[threading.Thread] = None
        self._last_sent_at: Optional[float] = None
        self._retry_after_s = 0.0
        self._last_error: Optional[BaseException] = None
        self._delivered = 0
        self._dropped = 0
        self._abandoned = 0
        # A full queue is said when it starts dropping, once per streak — at the end it would
        # come after the run was graded
        self._drop_reported = False

    def send(self, kind: OperatorAlertKind, level: OperatorAlertLevel, title: str,
             detail: str = '') -> None:
        """
        Queue one alert. Never blocks and never raises.

        Args:
            kind: Which moment it reports
            level: How urgent it is
            title: The one line a notification preview shows
            detail: Further lines

        Returns:
            None
        """
        try:
            alert = OperatorAlert(kind=kind, level=level, title=title, detail=detail,
                                  raised_at=datetime.now(timezone.utc))
            first_drop = False
            with self._condition:
                if self._closed:
                    self._abandoned += 1
                    return
                if len(self._queue) >= self._capacity:
                    self._evict_one()
                    first_drop = not self._drop_reported
                    self._drop_reported = True
                self._queue.append(alert)
                self._condition.notify()
            if first_drop:
                self._logger.warning(
                    f'📣 Operator alerts: the queue holds {self._capacity} and is full — the '
                    f'oldest alert below CRITICAL is dropped for each new one')
        except Exception as error:   # noqa: BLE001 — the never-raise contract, said in the pot
            # Called from the tick loop and from the abort path; a raise here would skip the
            # shutdown that cancels orders and writes the carry-over.
            self._logger.error(
                f'📣 Operator alert could not be queued ({type(error).__name__}: {error})')

    def start(self) -> None:
        """
        Start the delivery thread.

        Returns:
            None
        """
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name='operator-alerts', daemon=True)
        self._thread.start()

    def stop(self, flush_timeout_s: float) -> None:
        """
        Deliver what is queued, within a bound, then stop.

        Args:
            flush_timeout_s: How long the queue may take to drain

        Returns:
            None
        """
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=flush_timeout_s)
        with self._condition:
            undelivered = len(self._queue) + (1 if self._in_flight else 0)
        if undelivered:
            self._logger.warning(
                f'📣 Operator alerts: {undelivered} message(s) not delivered before the '
                f'session ended ({self._channel.describe()})')
        if self._dropped:
            self._logger.warning(
                f'📣 Operator alerts: {self._dropped} message(s) dropped — the queue held '
                f'{self._capacity} and was full')

    def get_delivered_count(self) -> int:
        """
        Messages the channel accepted.

        Returns:
            The count
        """
        return self._delivered

    def get_dropped_count(self) -> int:
        """
        Messages dropped because the queue was full.

        Returns:
            The count
        """
        return self._dropped

    def get_abandoned_count(self) -> int:
        """
        Messages given up on — the ladder stopped, or the channel had closed.

        Returns:
            The count
        """
        return self._abandoned

    def is_closed(self) -> bool:
        """
        Whether a terminal refusal closed the channel for the session.

        Returns:
            True once closed
        """
        return self._closed

    def _evict_one(self) -> None:
        """
        Make room in a full queue — the oldest alert below CRITICAL goes first.

        Called with the condition held. Only when every queued alert is CRITICAL does the oldest
        CRITICAL one go.
        """
        for index, queued in enumerate(self._queue):
            if queued.level is not OperatorAlertLevel.CRITICAL:
                del self._queue[index]
                break
        else:
            self._queue.popleft()
        self._dropped += 1

    def _run(self) -> None:
        """Deliver queued alerts until stopped and drained."""
        while True:
            with self._condition:
                while not self._queue and not self._stopping:
                    self._drop_reported = False
                    self._condition.wait()
                if not self._queue:
                    return
                alert = self._queue.popleft()
                self._in_flight = True
            self._pace()
            self._deliver(alert)
            with self._condition:
                self._in_flight = False

    def _pace(self) -> None:
        """Keep the configured gap after the previous message."""
        if self._last_sent_at is None:
            return
        remaining = self._min_interval_s - (self._clock() - self._last_sent_at)
        if remaining > 0:
            self._sleep(remaining)

    def _deliver(self, alert: OperatorAlert) -> None:
        """
        Send one alert through the ladder, and close the channel on a terminal refusal.

        Args:
            alert: The alert to send
        """
        try:
            text = render_alert_text(alert, self._session_label)
        except Exception as error:   # noqa: BLE001 — costs this one alert only
            self._abandoned += 1
            self._logger.error(
                f'📣 Operator alert could not be written ({type(error).__name__}: {error}) — '
                f'the next alert is tried')
            return
        self._retry_after_s = 0.0
        try:
            delivered = run_with_ladder(
                lambda: self._attempt(text), self._ladder, wait=self._wait_before_retry)
        except Exception as error:   # noqa: BLE001 — the thread ends only through stop()
            self._last_error = error
            self._logger.error(
                f'📣 Operator alert delivery failed ({type(error).__name__}: {error}) — the '
                f'next alert is tried')
            delivered = None
        self._last_sent_at = self._clock()
        if delivered:
            self._delivered += 1
            return
        self._abandoned += 1
        if (self._last_error is not None
                and self._ladder.classify(self._last_error) is ConnectionOutcome.TERMINAL):
            with self._condition:
                self._closed = True
                self._abandoned += len(self._queue)
                self._queue.clear()
            self._logger.warning(
                f'📣 Operator alerts are off for the rest of this session: '
                f'{self._channel.describe()} refused the message, and every later one would be '
                f'refused the same way. The liveness ping is unaffected.')

    def _attempt(self, text: str) -> bool:
        """
        One delivery attempt, remembering what failed for the decision after it.

        Args:
            text: The rendered message

        Returns:
            True once the channel accepted it
        """
        try:
            self._channel.send(text)
        except AlertChannelRateLimitedError as error:
            self._retry_after_s = error.retry_after_s
            self._last_error = error
            raise
        except Exception as error:   # noqa: BLE001 — classified by the ladder, never swallowed
            self._last_error = error
            raise
        return True

    def _wait_before_retry(self, delay_s: float) -> None:
        """
        Wait before the next attempt — the ladder's delay, or longer when the channel asked.

        Args:
            delay_s: The ladder's delay
        """
        self._sleep(max(delay_s, self._retry_after_s))
        self._retry_after_s = 0.0
