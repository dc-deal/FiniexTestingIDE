"""
Liveness Pinger — the ping vouches for the tick loop, not for the process (#235).

A monitor reads silence as death. These tests hold the pinger to that reading: it pings while
the loop passes, goes silent and says so when the loop stalls (the hung process a supervisor
cannot see), picks up again when the loop does, stops for good on a refusal, and never writes
a ping URL — a secret — into a log line. Without a ping URL it still watches the loop, so a
session with only the Telegram alerts on hears about a stall too; and nothing on its thread
ends it.
"""

import threading
import time
from typing import Tuple

import requests

from python.framework.autotrader.operator_alerts.liveness_pinger import LivenessPinger
from python.framework.autotrader.operator_alerts.loop_progress import LoopProgress
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.operator_alerts_config_types import LivenessPingConfig
from tests.autotrader.operator_alerts.operator_alert_doubles import (
    FAIL_URL,
    PING_URL,
    Monitor,
)
from tests.shared.recording_logger import RecordingLogger


class Clock:
    """The monotonic clock the loop progress reads, moved by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _pinger(monitor: Monitor, clock: Clock, stamp_file: str = '', ping_url: str = PING_URL,
            fail_url: str = FAIL_URL
            ) -> Tuple[LivenessPinger, LoopProgress, RecordingLogger, list]:
    progress = LoopProgress(clock=clock)
    logger = RecordingLogger()
    alerts: list = []
    config = LivenessPingConfig(enabled=True, interval_s=60.0, loop_stall_after_s=120.0,
                                liveness_stamp_file=stamp_file, request_timeout_s=5.0)
    pinger = LivenessPinger(ping_url, fail_url, config, progress, logger,
                            alert=lambda kind, level, title, detail: alerts.append((kind, level)),
                            http_get=monitor.get)
    return pinger, progress, logger, alerts


class TestThePingFollowsTheLoop:

    def test_no_ping_before_the_loop_has_passed(self):
        monitor = Monitor()
        pinger, _, _, _ = _pinger(monitor, Clock())
        pinger.beat()
        assert monitor.requests == []

    def test_pings_while_the_loop_passes(self):
        monitor, clock = Monitor(), Clock()
        pinger, progress, _, _ = _pinger(monitor, clock)
        progress.stamp()
        clock.now += 60.0
        pinger.beat()
        assert monitor.requests == [PING_URL]

    def test_a_stalled_loop_silences_the_ping_and_says_so_once(self):
        monitor, clock = Monitor(), Clock()
        pinger, progress, logger, alerts = _pinger(monitor, clock)
        progress.stamp()
        clock.now += 200.0                       # past the 120 s threshold
        pinger.beat()
        pinger.beat()

        assert monitor.requests == [FAIL_URL]    # the fail URL once, no ordinary ping
        assert alerts == [(OperatorAlertKind.TICK_LOOP_STALLED, OperatorAlertLevel.CRITICAL)]
        assert logger.levels().count('error') == 1

    def test_the_ping_resumes_with_the_loop(self):
        monitor, clock = Monitor(), Clock()
        pinger, progress, _, alerts = _pinger(monitor, clock)
        progress.stamp()
        clock.now += 200.0
        pinger.beat()
        progress.stamp()
        pinger.beat()

        assert monitor.requests == [FAIL_URL, PING_URL]
        assert alerts[-1] == (OperatorAlertKind.TICK_LOOP_RESUMED, OperatorAlertLevel.INFO)

    def test_without_a_ping_url_it_only_watches_the_loop(self, tmp_path):
        """Telegram on, the ping off: a stall still reaches the operator; nothing is pinged."""
        monitor, clock = Monitor(), Clock()
        stamp = tmp_path / 'eth_daily.alive'
        pinger, progress, logger, alerts = _pinger(
            monitor, clock, stamp_file=str(stamp), ping_url='', fail_url='')
        progress.stamp()
        pinger.beat()
        clock.now += 200.0
        pinger.beat()
        progress.stamp()
        pinger.beat()
        pinger.send_final(emergency=True)

        assert monitor.requests == []
        assert not stamp.exists(), 'the stamp file belongs to the ping'
        assert [kind for kind, _ in alerts] == [
            OperatorAlertKind.TICK_LOOP_STALLED, OperatorAlertKind.TICK_LOOP_RESUMED]
        assert 'pings' not in logger.text()
        assert not pinger.is_pinging()

    def test_the_watch_ends_with_its_thread(self):
        """Once the loop has returned, a shutdown longer than the threshold is not a stall."""
        monitor = Monitor()
        progress = LoopProgress()
        alerts: list = []
        config = LivenessPingConfig(enabled=True, interval_s=0.01, loop_stall_after_s=0.05)
        pinger = LivenessPinger(PING_URL, FAIL_URL, config, progress, RecordingLogger(),
                                alert=lambda *args: alerts.append(args[0]), http_get=monitor.get)
        progress.stamp()
        pinger.start()
        pinger.stop()                              # what the session does when the loop returns
        time.sleep(0.2)                            # four thresholds without a pass

        assert alerts == []
        assert 'liveness' not in [thread.name for thread in threading.enumerate()]


class TestTheMonitorsAnswer:

    def test_an_outage_is_said_once_per_streak(self):
        monitor, clock = Monitor(), Clock()
        pinger, progress, logger, _ = _pinger(monitor, clock)
        progress.stamp()
        monitor.fail_with = requests.ConnectionError(f'cannot reach {PING_URL}')
        pinger.beat()
        pinger.beat()
        monitor.fail_with = None
        pinger.beat()

        assert logger.levels().count('warning') == 1
        assert 'delivered again' in logger.text_at('info')
        assert monitor.requests == [PING_URL]

    def test_a_refused_url_stops_the_pings_for_the_session(self):
        monitor, clock = Monitor(status=404), Clock()
        pinger, progress, logger, alerts = _pinger(monitor, clock)
        progress.stamp()
        pinger.beat()
        pinger.beat()

        assert monitor.requests == [PING_URL]
        assert alerts == [(OperatorAlertKind.LIVENESS_PING_REFUSED, OperatorAlertLevel.CRITICAL)]
        assert 'HTTP 404' in logger.text_at('error')

    def test_a_refused_fail_url_does_not_silence_the_ping(self):
        """A wrong fail URL costs the instant alarm, never the ordinary pings."""
        clock = Clock()
        monitor = Monitor()
        pinger, progress, logger, alerts = _pinger(monitor, clock)
        monitor.status = 404                      # the monitor knows no fail URL
        progress.stamp()
        clock.now += 200.0
        pinger.beat()                             # stall → the fail URL, refused
        monitor.status = 200
        progress.stamp()
        pinger.beat()                             # the loop passes again

        assert monitor.requests[-1] == PING_URL
        assert (OperatorAlertKind.LIVENESS_PING_REFUSED, OperatorAlertLevel.WARNING) in alerts
        assert 'fail URL refused' in logger.text_at('error')

    def test_a_url_the_http_stack_cannot_request_is_refused(self):
        """A double dot in the host passes the parser and fails on the request — not a death."""
        monitor, clock = Monitor(), Clock()
        pinger, progress, logger, alerts = _pinger(monitor, clock)
        progress.stamp()
        monitor.fail_with = ValueError(f"Failed to parse: '{PING_URL}', label empty")
        pinger.beat()
        pinger.beat()
        pinger.send_final(emergency=False)

        assert alerts == [(OperatorAlertKind.LIVENESS_PING_REFUSED, OperatorAlertLevel.CRITICAL)]
        assert 'ValueError' in logger.text_at('error')
        assert 'hc-ping' not in logger.text()

    def test_the_thread_survives_a_failure_nobody_classified(self):
        """Only stop() ends the thread: an unexpected failure is said once, the next beat runs."""
        monitor = Monitor()
        logger = RecordingLogger()
        progress = LoopProgress()
        progress.stamp()
        calls = {'n': 0}

        class OnceBrokenProgress:
            def seconds_since_last_pass(self):
                calls['n'] += 1
                if calls['n'] <= 2:
                    raise RuntimeError('broken')
                return progress.seconds_since_last_pass()

        config = LivenessPingConfig(enabled=True, interval_s=0.01, loop_stall_after_s=60.0)
        pinger = LivenessPinger(PING_URL, FAIL_URL, config, OnceBrokenProgress(), logger,
                                alert=lambda *args: None, http_get=monitor.get)
        pinger.start()
        deadline = time.monotonic() + 5.0
        while not monitor.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        pinger.stop()

        assert monitor.requests, 'the beat after the failure pinged'
        assert logger.levels().count('error') == 1
        assert 'RuntimeError' in logger.text_at('error')

    def test_no_log_line_names_a_url(self):
        monitor, clock = Monitor(), Clock()
        pinger, progress, logger, _ = _pinger(monitor, clock)
        progress.stamp()
        monitor.fail_with = requests.ConnectionError(f'cannot reach {PING_URL}')
        pinger.beat()
        monitor.fail_with = None
        monitor.status = 410
        pinger.beat()
        clock.now += 500.0
        pinger.beat()

        assert logger.lines
        assert 'hc-ping' not in logger.text()


class TestTheLastWord:

    def test_an_emergency_end_pings_the_fail_url(self):
        monitor = Monitor()
        pinger, _, _, _ = _pinger(monitor, Clock())
        pinger.send_final(emergency=True)
        assert monitor.requests == [FAIL_URL]

    def test_any_other_end_of_a_loop_that_passed_sends_one_last_ping(self):
        monitor = Monitor()
        pinger, progress, _, _ = _pinger(monitor, Clock())
        progress.stamp()
        pinger.send_final(emergency=False)
        assert monitor.requests == [PING_URL]

    def test_a_loop_that_never_passed_gets_no_last_ping(self):
        """A refused boot vouches for nothing — its silence raises the alarm after the grace."""
        monitor = Monitor()
        pinger, _, _, _ = _pinger(monitor, Clock())
        pinger.send_final(emergency=False)
        assert monitor.requests == []


def test_the_stamp_file_is_rewritten_whole(tmp_path):
    monitor, clock = Monitor(), Clock()
    stamp = tmp_path / 'watch' / 'eth_daily.alive'
    pinger, progress, _, _ = _pinger(monitor, clock, stamp_file=str(stamp))
    progress.stamp()
    pinger.beat()

    written = stamp.read_text().strip()
    assert written.endswith('+00:00')                  # a UTC instant
    assert not list(stamp.parent.glob('*.tmp'))         # renamed into place, nothing left over


def test_a_stamp_file_that_cannot_be_written_is_said_once_and_the_ping_goes_on(tmp_path):
    monitor, clock = Monitor(), Clock()
    blocked = tmp_path / 'not_a_dir'
    blocked.write_text('a file where a directory would have to be')
    pinger, progress, logger, _ = _pinger(monitor, clock, stamp_file=str(blocked / 'x.alive'))
    progress.stamp()
    pinger.beat()
    pinger.beat()

    assert logger.levels().count('warning') == 1
    assert monitor.requests == [PING_URL, PING_URL]
