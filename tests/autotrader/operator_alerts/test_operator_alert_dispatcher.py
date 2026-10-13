"""
Operator Alert Dispatcher — the queue and thread between the session and its channel (#235).

The tick loop must never wait on a network, the channel must never be flooded, a request to
slow down must be honoured, and the two kinds of failure must end differently: an outage costs
one message, a refusal closes the channel for the session — said once, never silently.
"""

import threading
import time
from typing import List

from python.framework.autotrader.operator_alerts import (
    operator_alert_dispatcher as dispatcher_module,
)
from python.framework.autotrader.operator_alerts.abstract_alert_channel import (
    AbstractAlertChannel,
)
from python.framework.autotrader.operator_alerts.operator_alert_dispatcher import (
    OperatorAlertDispatcher,
)
from python.framework.exceptions.connection_errors import (
    ConnectionAttemptFailedError,
    ConnectionInadmissibleError,
)
from python.framework.exceptions.operator_alert_errors import AlertChannelRateLimitedError
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.config_types.operator_alerts_config_types import TelegramAlertsConfig
from tests.autotrader.operator_alerts.operator_alert_doubles import ScriptedChannel
from tests.shared.recording_logger import RecordingLogger

LABEL = 'eth_daily · kraken_spot ETHUSD'


class FakeTime:
    """A clock that moves only when somebody sleeps — so every wait is recorded, none spent."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: List[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _dispatcher(channel: AbstractAlertChannel, fake: FakeTime, logger: RecordingLogger,
                capacity: int = 100, budget: int = 3) -> OperatorAlertDispatcher:
    config = TelegramAlertsConfig(
        enabled=True, min_send_interval_s=3.0, queue_capacity=capacity,
        connection=ConnectionPolicy(initial_delay_s=1.0, max_delay_s=1.0, jitter=False,
                                    attempt_budget=budget, request_timeout_s=1.0))
    return OperatorAlertDispatcher(channel, config, logger, LABEL,
                                   clock=fake.clock, sleep=fake.sleep)


def _send(dispatcher: OperatorAlertDispatcher, title: str) -> None:
    dispatcher.send(OperatorAlertKind.SAFETY_BLOCKED, OperatorAlertLevel.WARNING, title)


class TestTheLoopNeverWaits:

    def test_send_returns_at_once_while_the_channel_hangs(self):
        gate = threading.Event()
        dispatcher = _dispatcher(ScriptedChannel(gate=gate), FakeTime(), RecordingLogger())
        dispatcher.start()
        started = time.monotonic()
        for n in range(5):
            _send(dispatcher, f'alert {n}')
        assert time.monotonic() - started < 0.05
        gate.set()
        dispatcher.stop(flush_timeout_s=5.0)

    def test_a_full_queue_drops_the_oldest_and_says_so_at_once(self):
        channel = ScriptedChannel()
        logger = RecordingLogger()
        dispatcher = _dispatcher(channel, FakeTime(), logger, capacity=2)
        for title in ('first', 'second', 'third', 'fourth'):
            _send(dispatcher, title)
        assert 'queue holds 2 and is full' in logger.text_at('warning')   # before the grading
        dispatcher.start()
        dispatcher.stop(flush_timeout_s=5.0)

        assert dispatcher.get_dropped_count() == 2
        assert channel.titles() == ['⚠️ third', '⚠️ fourth']
        assert logger.text_at('warning').count('is full') == 1          # once per streak

    def test_a_critical_alert_outranks_the_rest_when_the_queue_is_full(self):
        channel = ScriptedChannel()
        dispatcher = _dispatcher(channel, FakeTime(), RecordingLogger(), capacity=2)
        dispatcher.send(OperatorAlertKind.EMERGENCY_STOP, OperatorAlertLevel.CRITICAL,
                        'EMERGENCY STOP')
        _send(dispatcher, 'breaker')
        _send(dispatcher, 'breaker again')
        dispatcher.start()
        dispatcher.stop(flush_timeout_s=5.0)

        assert channel.titles() == ['⛔ EMERGENCY STOP', '⚠️ breaker again']

    def test_send_never_raises(self, monkeypatch):
        """Called from the abort path — a raise there would skip the shutdown."""
        def broken(**fields):
            raise RuntimeError('defect while building the alert')

        monkeypatch.setattr(dispatcher_module, 'OperatorAlert', broken)
        logger = RecordingLogger()
        dispatcher = _dispatcher(ScriptedChannel(), FakeTime(), logger)

        _send(dispatcher, 'never raised')

        assert 'could not be queued (RuntimeError' in logger.text_at('error')


class TestThePace:

    def test_messages_keep_the_configured_gap(self):
        fake = FakeTime()
        dispatcher = _dispatcher(ScriptedChannel(), fake, RecordingLogger())
        for n in range(3):
            _send(dispatcher, f'alert {n}')
        dispatcher.start()
        dispatcher.stop(flush_timeout_s=5.0)
        assert fake.sleeps == [3.0, 3.0]

    def test_a_request_to_slow_down_is_honoured(self):
        fake = FakeTime()
        channel = ScriptedChannel([AlertChannelRateLimitedError('slow down', 9.0)])
        dispatcher = _dispatcher(channel, fake, RecordingLogger())
        _send(dispatcher, 'alert')
        dispatcher.start()
        dispatcher.stop(flush_timeout_s=5.0)

        assert fake.sleeps == [9.0]          # not the ladder's 1.0
        assert dispatcher.get_delivered_count() == 1


class TestTheTwoFailures:

    def test_an_outage_costs_one_message_and_the_next_is_tried(self):
        logger = RecordingLogger()
        outage = ConnectionAttemptFailedError('Telegram unreachable (ConnectionError)')
        channel = ScriptedChannel([outage, outage, outage])   # the whole budget of 3
        dispatcher = _dispatcher(channel, FakeTime(), logger)
        _send(dispatcher, 'lost')
        _send(dispatcher, 'kept')
        dispatcher.start()
        dispatcher.stop(flush_timeout_s=5.0)

        assert channel.titles() == ['⚠️ kept']
        assert dispatcher.get_abandoned_count() == 1
        assert not dispatcher.is_closed()
        assert logger.levels().count('error') == 1      # the ladder's give-up, said once

    def test_a_refusal_closes_the_channel_for_the_session(self):
        logger = RecordingLogger()
        refusal = ConnectionAttemptFailedError('Telegram refused sendMessage (401)', terminal=True)
        channel = ScriptedChannel([refusal])
        dispatcher = _dispatcher(channel, FakeTime(), logger)
        _send(dispatcher, 'refused')
        dispatcher.start()
        dispatcher.stop(flush_timeout_s=5.0)
        _send(dispatcher, 'after the refusal')

        assert dispatcher.is_closed()
        assert channel.attempts == 1                    # no retry of a refusal
        assert dispatcher.get_abandoned_count() == 2
        assert logger.levels().count('error') == 1
        assert 'off for the rest of this session' in logger.text_at('warning')

    def test_the_thread_survives_a_failure_nobody_classified(self):
        """Only stop() ends the thread — an emergency alert after the failure still goes out."""
        logger = RecordingLogger()
        channel = ScriptedChannel([ConnectionInadmissibleError('cannot continue')])
        dispatcher = _dispatcher(channel, FakeTime(), logger)
        _send(dispatcher, 'first')
        dispatcher.send(OperatorAlertKind.EMERGENCY_STOP, OperatorAlertLevel.CRITICAL,
                        'EMERGENCY STOP')
        dispatcher.start()
        dispatcher.stop(flush_timeout_s=5.0)

        assert 'delivery failed' in logger.text_at('error')
        assert channel.titles() == ['⛔ EMERGENCY STOP']

    def test_an_alert_that_cannot_be_written_costs_only_itself(self, monkeypatch):
        """A text that cannot be rendered is dropped and said — the channel stays open."""
        logger = RecordingLogger()
        channel = ScriptedChannel()
        real_render = dispatcher_module.render_alert_text
        rendered = []

        def broken_once(alert, label):
            rendered.append(alert.title)
            if len(rendered) == 1:
                raise KeyError('level')
            return real_render(alert, label)

        monkeypatch.setattr(dispatcher_module, 'render_alert_text', broken_once)
        dispatcher = _dispatcher(channel, FakeTime(), logger)
        _send(dispatcher, 'first')
        dispatcher.send(OperatorAlertKind.EMERGENCY_STOP, OperatorAlertLevel.CRITICAL,
                        'EMERGENCY STOP')
        dispatcher.start()
        dispatcher.stop(flush_timeout_s=5.0)

        assert 'could not be written' in logger.text_at('error')
        assert channel.titles() == ['⛔ EMERGENCY STOP']

    def test_what_could_not_go_out_is_said_at_the_end(self):
        gate = threading.Event()
        logger = RecordingLogger()
        dispatcher = _dispatcher(ScriptedChannel(gate=gate), FakeTime(), logger)
        _send(dispatcher, 'stuck')
        dispatcher.start()
        dispatcher.stop(flush_timeout_s=0.1)
        gate.set()
        assert '1 message(s) not delivered' in logger.text_at('warning')


def test_every_message_names_its_speaker_and_when_it_was_raised():
    channel = ScriptedChannel()
    dispatcher = _dispatcher(channel, FakeTime(), RecordingLogger())
    dispatcher.send(OperatorAlertKind.STARTUP_FAILED, OperatorAlertLevel.CRITICAL,
                    'STARTUP FAILED', 'no tick source')
    dispatcher.start()
    dispatcher.stop(flush_timeout_s=5.0)

    first, detail, speaker = channel.delivered[0].splitlines()
    assert first == '⛔ STARTUP FAILED'
    assert detail == 'no tick source'
    assert speaker.startswith(f'{LABEL} · ') and speaker.endswith(' UTC')
