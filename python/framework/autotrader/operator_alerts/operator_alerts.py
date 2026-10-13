"""
FiniexTestingIDE - Operator Alerts

The session's out-of-band channels to its operator (#235), as the session holds them: the alert
dispatcher and the liveness pinger, either of which may be off, and the loop progress the pinger
vouches for. With both off every call is a no-op — the state of a mock session, of a test, and
of every profile that has not switched them on — so a caller never asks whether alerts exist.
"""

from typing import Optional

from python.framework.autotrader.operator_alerts.liveness_pinger import LivenessPinger
from python.framework.autotrader.operator_alerts.loop_progress import LoopProgress
from python.framework.autotrader.operator_alerts.operator_alert_dispatcher import (
    OperatorAlertDispatcher,
)
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)

# How long the queued alerts may take to go out when the session ends. Long enough for a
# handful of messages at the configured pace; short enough that a dead channel does not hold a
# supervised restart.
FLUSH_TIMEOUT_S = 15.0


class OperatorAlerts:
    """
    The alert dispatcher, the liveness pinger and the loop progress, as one handle.

    Args:
        dispatcher: The Telegram alert dispatcher, None when alerts are off
        pinger: The liveness pinger, None when both channels are off — with the ping off and
            Telegram on it pings nothing and only watches the loop
        loop_progress: The tick loop's most recent pass — stamped by the loop either way
    """

    def __init__(
        self,
        dispatcher: Optional[OperatorAlertDispatcher],
        pinger: Optional[LivenessPinger],
        loop_progress: LoopProgress,
    ):
        self._dispatcher = dispatcher
        self._pinger = pinger
        self._loop_progress = loop_progress
        self._finished = False

    def send(self, kind: OperatorAlertKind, level: OperatorAlertLevel, title: str,
             detail: str = '') -> None:
        """
        Queue one alert — a no-op when alerts are off. Never blocks and never raises.

        Args:
            kind: Which moment it reports
            level: How urgent it is
            title: The one line a notification preview shows
            detail: Further lines

        Returns:
            None
        """
        if self._dispatcher is not None:
            self._dispatcher.send(kind, level, title, detail)

    def get_loop_progress(self) -> LoopProgress:
        """
        The loop progress the tick loop stamps on every pass.

        Returns:
            The shared LoopProgress
        """
        return self._loop_progress

    def start_liveness(self) -> None:
        """
        Start the liveness ping — at the tick loop's entry, when there is a loop to vouch for.

        Returns:
            None
        """
        if self._pinger is not None:
            self._pinger.start()

    def loop_ended(self) -> None:
        """
        Stop judging the loop once it has returned — a shutdown is not a stall.

        The reports, the order cleanup and the venue's last read can take longer than the stall
        threshold, and none of them stamps the loop's progress.

        Returns:
            None
        """
        if self._pinger is not None:
            self._pinger.stop()

    def finish(self, emergency: bool, flush_timeout_s: float = FLUSH_TIMEOUT_S) -> None:
        """
        The session's last word: the final ping or the fail ping, then the queued alerts.

        Once only: a shutdown that crashes after the report has finished must not ping twice.

        Args:
            emergency: Whether the session ended in an emergency the operator did not start
            flush_timeout_s: How long the queued alerts may take to go out

        Returns:
            None
        """
        if self._finished:
            return
        self._finished = True
        try:
            if self._pinger is not None:
                self._pinger.stop()
                self._pinger.send_final(emergency)
        finally:
            # The queued alerts go out whatever happened to the ping
            if self._dispatcher is not None:
                self._dispatcher.stop(flush_timeout_s)

    def describe(self) -> str:
        """
        Which channels are on, for the startup line.

        Returns:
            E.g. 'telegram on · liveness ping on'
        """
        telegram = 'on' if self._dispatcher is not None else 'off'
        liveness = 'on' if self._pinger is not None and self._pinger.is_pinging() else 'off'
        return f'telegram {telegram} · liveness ping {liveness}'
