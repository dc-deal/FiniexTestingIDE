"""
Signal Staleness Alerter — a SIGNAL input that turns stale reaches the operator (#235).

The orchestrator owns the stale edge and fires it into the decision logic in both pipelines. The
alerter turns that edge into operator alerts: one message when an input goes stale, one when it
has stayed fresh for the quiet period — never one per pass, and an input that flaps inside the
quiet period stays one episode.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from python.framework.autotrader.operator_alerts.signal_staleness_alerter import (
    SignalStalenessAlerter,
)
from python.framework.types.autotrader_types.operator_alert_types import OperatorAlertKind
from tests.shared.operator_alert_recorder import OperatorAlertRecorder

QUIET_S = 300.0
START = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


class _Orchestrator:
    """The two reads the alerter makes: the flip count, and which workers are stale now."""

    def __init__(self):
        self.flips = 0
        self.stale: List[str] = []

    def turn_stale(self, name: str) -> None:
        self.flips += 1
        self.stale.append(name)

    def turn_fresh(self, name: str) -> None:
        self.flips += 1
        self.stale.remove(name)

    def get_signal_stale_flip_count(self) -> int:
        return self.flips

    def get_stale_signal_workers(self) -> List[str]:
        return list(self.stale)


class _Clock:
    """The canonical clock, None until a pass has set it."""

    def __init__(self, now: Optional[datetime] = START):
        self.now = now

    def __call__(self) -> Optional[datetime]:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _alerter():
    orchestrator, alerts, clock = _Orchestrator(), OperatorAlertRecorder(), _Clock()
    return SignalStalenessAlerter(orchestrator, alerts, QUIET_S, clock), orchestrator, alerts, clock


def test_a_stale_input_is_said_once_and_its_recovery_after_the_quiet_period():
    alerter, orchestrator, alerts, clock = _alerter()
    orchestrator.turn_stale('sentiment')
    for _ in range(5):
        alerter.check()
    assert alerts.kinds == [OperatorAlertKind.SIGNAL_STALE]
    assert alerts.titles == ['Signal input stale — sentiment']

    orchestrator.turn_fresh('sentiment')
    alerter.check()
    clock.advance(QUIET_S - 1)
    alerter.check()
    assert alerts.kinds == [OperatorAlertKind.SIGNAL_STALE], 'the quiet period has not passed'

    clock.advance(1)
    alerter.check()
    alerter.check()
    assert alerts.kinds == [OperatorAlertKind.SIGNAL_STALE, OperatorAlertKind.SIGNAL_RECOVERED]


def test_an_input_that_flaps_inside_the_quiet_period_stays_one_episode():
    alerter, orchestrator, alerts, clock = _alerter()
    for _ in range(3):
        orchestrator.turn_stale('sentiment')
        alerter.check()
        orchestrator.turn_fresh('sentiment')
        alerter.check()
        clock.advance(60)
    clock.advance(QUIET_S)
    alerter.check()
    assert alerts.kinds == [OperatorAlertKind.SIGNAL_STALE, OperatorAlertKind.SIGNAL_RECOVERED]


def test_each_input_is_its_own_episode():
    alerter, orchestrator, alerts, _clock = _alerter()
    orchestrator.turn_stale('sentiment')
    alerter.check()
    orchestrator.turn_stale('news')
    alerter.check()
    assert alerts.titles == ['Signal input stale — sentiment', 'Signal input stale — news']


def test_nothing_is_said_before_the_clock_is_set():
    """The first passes can run before the canonical clock has a value; the edge waits for it."""
    orchestrator, alerts, clock = _Orchestrator(), OperatorAlertRecorder(), _Clock(now=None)
    alerter = SignalStalenessAlerter(orchestrator, alerts, QUIET_S, clock)
    orchestrator.turn_stale('sentiment')
    alerter.check()
    assert alerts.kinds == []

    clock.now = START
    alerter.check()
    assert alerts.kinds == [OperatorAlertKind.SIGNAL_STALE]
