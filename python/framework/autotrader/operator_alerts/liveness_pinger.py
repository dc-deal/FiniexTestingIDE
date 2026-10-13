"""
FiniexTestingIDE - Liveness Pinger

The outward sign that the tick loop is still passing (#235) — the alarm a dead process cannot
send itself. A monitoring service (healthchecks.io and its kin) expects a ping every period and
raises the alarm when one is missing; this thread sends it.

It vouches for the LOOP, not for the process: the ping goes out only while the loop has come
round within `loop_stall_after_s`. A process that is alive but hung therefore goes silent too —
the one failure a supervisor cannot see, because nothing crashes and nothing exits. On a stall the
pings stop, the fail URL is pinged once and the operator is alerted; when the loop comes round
again the pings resume and the operator hears that too.

Without a ping URL — the ping switched off while the Telegram alerts are on — the thread only
watches the loop: a stall still reaches the operator, and nothing is pinged or written. The watch
ends with the loop: once the session is shutting down, a pass that no longer comes is not a stall.

A failed ping is not retried: the next one is due within the interval anyway, so the cadence is
the ladder (§43). A transport failure is said once per streak; a refusal — the URL is wrong, the
check was deleted — stops the pings for the session, because every later one would be refused
the same way, and says so in the session log and to the operator. Nothing on this thread may end
it: a failure nobody classified is said in the session log, and the next interval comes anyway.

The URLs are secrets — whoever holds one can fake a liveness ping — so no log line names them.
"""

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from python.framework.autotrader.operator_alerts.loop_progress import LoopProgress
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.operator_alerts_config_types import LivenessPingConfig
from python.framework.utils.connection_ladder import is_terminal_status

# Sends one operator alert: kind, level, title, detail.
AlertSender = Callable[[OperatorAlertKind, OperatorAlertLevel, str, str], None]


class LivenessPinger:
    """
    Pings the monitoring service while the tick loop passes.

    Args:
        ping_url: The monitor's ping URL — a secret; '' watches the loop without pinging
        fail_url: The URL that tells the monitor the session failed, '' when it has none
        config: Interval, stall threshold, stamp file and request timeout
        loop_progress: The tick loop's most recent pass
        logger: The session logger
        alert: Sends an operator alert — a stall and a refused ping are worth one
        http_get: The GET call — `requests.get`, injected by tests
    """

    def __init__(
        self,
        ping_url: str,
        fail_url: str,
        config: LivenessPingConfig,
        loop_progress: LoopProgress,
        logger: AbstractLogger,
        alert: AlertSender,
        http_get: Optional[Callable[..., Any]] = None,
    ):
        self._ping_url = ping_url
        self._fail_url = fail_url
        self._interval_s = config.interval_s
        self._stall_after_s = config.loop_stall_after_s
        # The stamp file belongs to the ping: a pinger that only watches the loop writes none
        self._stamp_file = config.liveness_stamp_file if ping_url else ''
        self._request_timeout_s = config.request_timeout_s
        self._loop_progress = loop_progress
        self._logger = logger
        self._alert = alert
        self._http_get = http_get if http_get is not None else requests.get
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stalled = False
        # Per URL: a refused fail URL must not silence the healthy ping URL
        self._ping_refused = False
        self._fail_refused = False
        self._failing = False
        self._file_failing = False
        self._beat_failing = False
        self._pings_delivered = 0

    def start(self) -> None:
        """
        Start pinging — the first ping goes out one interval after the loop's first pass.

        Returns:
            None
        """
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name='liveness', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """
        Stop pinging and judging the loop, and wait for the thread — once the loop has ended.

        Returns:
            None
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._request_timeout_s + 2.0)
            self._thread = None

    def send_final(self, emergency: bool) -> None:
        """
        The session's last word to the monitor — called after stop().

        An emergency end pings the fail URL, so the alarm comes at once rather than after the
        monitor's grace period. Any other end of a loop that passed sends one last ping; the
        silence after it is what a monitor reads as the end, which is why a deliberate stop is
        paused there. A session whose loop never passed sends nothing — there is no loop to
        vouch for, and its silence raises the monitor's alarm after the grace period.

        Args:
            emergency: Whether the session ended in an emergency the operator did not start

        Returns:
            None
        """
        if emergency:
            if self._fail_url and not self._fail_refused:
                self._ping(self._fail_url, fail=True)
            return
        if not self._ping_url or self._stalled or self._ping_refused:
            return
        if self._loop_progress.seconds_since_last_pass() is None:
            return
        self._ping(self._ping_url, fail=False)

    def is_pinging(self) -> bool:
        """
        Whether this pinger pings a monitor, or only watches the loop.

        Returns:
            True when it has a ping URL
        """
        return bool(self._ping_url)

    def ping_once(self) -> bool:
        """
        One ping, now — the operator's check that the monitor answers.

        Returns:
            True when the monitor accepted it
        """
        return self._ping(self._ping_url, fail=False)

    def get_pings_delivered(self) -> int:
        """
        Pings the monitor accepted.

        Returns:
            The count
        """
        return self._pings_delivered

    def _run(self) -> None:
        """Beat once per interval until stopped — a failure nobody classified is said, once."""
        while not self._stop.wait(self._interval_s):
            try:
                self.beat()
            except Exception as error:   # noqa: BLE001 — only stop() may end this thread
                if not self._beat_failing:
                    self._beat_failing = True
                    self._logger.error(
                        f'❌ Liveness ping failed unexpectedly ({type(error).__name__}) — the '
                        f'thread goes on, but this session may not reach its monitor')
                continue
            self._beat_failing = False

    def beat(self) -> None:
        """
        One interval's work: ping when the loop is passing; report a stall and a recovery once.

        The pinger's thread calls it once per interval; a test calls it to hold one interval
        without waiting for it.

        Returns:
            None
        """
        since = self._loop_progress.seconds_since_last_pass()
        if since is None:
            return
        if since > self._stall_after_s:
            if not self._stalled:
                self._report_stall(since)
            return
        if self._stalled:
            self._stalled = False
            resumed = ' — liveness pings resumed' if self._ping_url else ''
            self._logger.info(f'📡 Tick loop passing again{resumed}')
            self._alert(OperatorAlertKind.TICK_LOOP_RESUMED, OperatorAlertLevel.INFO,
                        'Tick loop passing again',
                        'Liveness pings resumed.' if self._ping_url else '')
        if not self._ping_url:
            return
        self._write_stamp_file()
        if not self._ping_refused:
            self._ping(self._ping_url, fail=False)

    def _report_stall(self, since: float) -> None:
        """
        Stop vouching for a loop that has not come round, and say so.

        Args:
            since: Seconds since the loop's last pass
        """
        self._stalled = True
        stopped = ' — liveness pings stopped' if self._ping_url else ''
        self._logger.error(
            f'🔇 Tick loop stalled: no pass for {since:.0f}s (threshold '
            f'{self._stall_after_s:.0f}s){stopped}')
        pings = '; liveness pings stopped' if self._ping_url else ''
        self._alert(OperatorAlertKind.TICK_LOOP_STALLED, OperatorAlertLevel.CRITICAL,
                    'TICK LOOP STALLED',
                    f'No pass for {since:.0f}s{pings}. The process is alive but not trading — '
                    f'check the machine.')
        if self._fail_url and not self._fail_refused:
            self._ping(self._fail_url, fail=True)

    def _ping(self, url: str, fail: bool) -> bool:
        """
        One request to the monitor, classified.

        Args:
            url: The ping or the fail URL
            fail: Whether it is the fail URL — a refusal stops only the URL refused

        Returns:
            True when the monitor accepted it
        """
        try:
            response = self._http_get(url, timeout=self._request_timeout_s)
        except requests.RequestException as error:
            self._note_failure(type(error).__name__)
            return False
        except Exception as error:       # noqa: BLE001 — e.g. a URL the HTTP stack cannot parse
            # Not a transport failure: every later request would fail the same way
            self._refuse(type(error).__name__, fail)
            return False
        status = response.status_code
        if 200 <= status < 300:
            self._pings_delivered += 1
            if self._failing:
                self._failing = False
                self._logger.info('📡 Liveness ping delivered again')
            return True
        if is_terminal_status(status):
            self._refuse(f'HTTP {status}', fail)
            return False
        self._note_failure(f'HTTP {status}')
        return False

    def _note_failure(self, kind: str) -> None:
        """
        Say once per streak that pings are not getting through.

        Args:
            kind: What failed, without the URL
        """
        if self._failing:
            return
        self._failing = True
        self._logger.warning(
            f'📡 Liveness ping failed ({kind}) — the next one is due in {self._interval_s:.0f}s; '
            f'the monitor raises its alarm if they keep failing')

    def _refuse(self, answer: str, fail: bool) -> None:
        """
        Stop using a URL the monitor refuses, or that cannot be requested, and say so where it
        will be seen.

        A refused fail URL stops only the fail pings: the ordinary pings still vouch for the
        loop, and the monitor still notices silence — only an emergency end loses its instant
        alarm.

        Args:
            answer: The monitor's answer (`HTTP 404`), or the error a request could not get past
            fail: Whether the refused URL is the fail URL
        """
        if fail:
            self._fail_refused = True
            self._logger.error(
                f'❌ Liveness fail URL refused ({answer}) — an emergency end now reaches the '
                f'monitor only as silence after its grace period; the ordinary pings go on. Fix '
                f'the fail URL in the liveness credentials.')
            self._alert(OperatorAlertKind.LIVENESS_PING_REFUSED, OperatorAlertLevel.WARNING,
                        'Liveness fail URL refused',
                        f'The fail URL was refused ({answer}). The ordinary pings go on; an '
                        f'emergency end now reaches the monitor only as silence.')
            return
        self._ping_refused = True
        self._logger.error(
            f'❌ Liveness ping refused ({answer}) — the ping URL is wrong or the check was '
            f'deleted. Pings stopped for this session, so the monitor will raise its alarm. Fix '
            f'the URL in the liveness credentials and restart.')
        self._alert(OperatorAlertKind.LIVENESS_PING_REFUSED, OperatorAlertLevel.CRITICAL,
                    'Liveness ping refused',
                    f'The ping URL was refused ({answer}): it is wrong or the check was deleted. '
                    f'Pings stopped, so the monitor will report this session silent.')

    def _write_stamp_file(self) -> None:
        """Rewrite the stamp file with the time now, atomically — when one is configured."""
        if not self._stamp_file:
            return
        path = Path(self._stamp_file)
        try:
            staging = path.with_name(path.name + '.tmp')
            path.parent.mkdir(parents=True, exist_ok=True)
            staging.write_text(datetime.now(timezone.utc).isoformat() + '\n')
            os.replace(staging, path)
        except (OSError, ValueError) as error:
            if not self._file_failing:
                self._file_failing = True
                self._logger.warning(
                    f'📡 Liveness stamp file {path} could not be written ({error}) — a local '
                    f'watcher will read this session as silent')
            return
        self._file_failing = False
