"""
FiniexTestingIDE - Signal Staleness Alerter (#235)

Tells the operator when a SIGNAL input of a running session turns stale, and when it has stayed
fresh again. The orchestrator owns the stale edge and fires it into the decision logic in both
pipelines; this only turns that edge into operator alerts, one episode per input.
"""

from datetime import datetime
from typing import Callable, Dict, Optional, Set

from python.framework.autotrader.operator_alerts.alert_episode import (
    AlertEpisode,
    all_clear_detail,
)
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


class SignalStalenessAlerter:
    """
    Turns the orchestrator's signal stale edges into operator alerts.

    Args:
        worker_orchestrator: Owns the stale edge and knows which signal workers are stale
        operator_alerts: The out-of-band channels to the operator
        all_clear_after_s: How long an input must stay fresh before its all-clear goes out
        clock_fn: The canonical clock — None before it is first set
    """

    def __init__(
        self,
        worker_orchestrator: WorkerOrchestrator,
        operator_alerts: OperatorAlerts,
        all_clear_after_s: float,
        clock_fn: Callable[[], Optional[datetime]],
    ):
        self._worker_orchestrator = worker_orchestrator
        self._operator_alerts = operator_alerts
        self._all_clear_after_s = all_clear_after_s
        self._clock_fn = clock_fn
        self._seen_signal_stale_flips = 0
        self._stale_signals_seen: Set[str] = set()
        self._signal_alerts: Dict[str, AlertEpisode] = {}

    def check(self) -> None:
        """
        Tell the operator when a SIGNAL input turns stale, and when it has stayed fresh again.

        The orchestrator owns the stale edge — it fires `on_signal_stale` there, in both
        pipelines. This only compares the orchestrator's flip count with the one it last
        saw, so a pass with nothing new costs one integer comparison, and reads the state itself
        only when something moved. Each worker is its own episode (#235): its all-clear waits
        until it has stayed fresh for the quiet period.
        """
        now = self._clock_fn()
        if now is None:
            return
        flips = self._worker_orchestrator.get_signal_stale_flip_count()
        if flips != self._seen_signal_stale_flips:
            self._seen_signal_stale_flips = flips
            stale_now = set(self._worker_orchestrator.get_stale_signal_workers())
            for name in sorted(stale_now - self._stale_signals_seen):
                episode = self._signal_alerts.setdefault(
                    name, AlertEpisode(self._all_clear_after_s))
                if episode.opens(now):
                    self._operator_alerts.send(
                        OperatorAlertKind.SIGNAL_STALE, OperatorAlertLevel.WARNING,
                        f'Signal input stale — {name}',
                        'The decision now reads it as stale; the strategy decides what that '
                        'means.')
            for name in sorted(self._stale_signals_seen - stale_now):
                self._signal_alerts[name].clears(now)
            self._stale_signals_seen = stale_now
        for name, episode in self._signal_alerts.items():
            if episode.is_open() and episode.all_clear_due(now):
                self._operator_alerts.send(
                    OperatorAlertKind.SIGNAL_RECOVERED, OperatorAlertLevel.INFO,
                    f'Signal input fresh again — {name}',
                    all_clear_detail(episode))
