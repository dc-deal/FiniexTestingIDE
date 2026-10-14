"""
FiniexTestingIDE - Alert Episode

One symptom's place in the operator's inbox (#235): opened by the first edge, closed only after
the symptom has stayed away for `all_clear_after_s`.

A symptom that flaps — an account value hovering at a breaker limit, a quiet market crossing its
staleness threshold every few minutes — would otherwise send a pair of messages per crossing.
Measured on the breaker: 600 ticks of noise around a drawdown limit gave 28 alerts. With an
episode the operator hears one message when the symptom starts and one when it has really
ended, and the message in between stays true, because a recurrence before the all-clear keeps
the episode open rather than sending its opening message again.

Times come from the session's canonical clock (§9) — a tick's time or the heartbeat's.
"""

from datetime import datetime
from typing import Optional


class AlertEpisode:
    """
    The alert state of one symptom.

    Args:
        all_clear_after_s: How long the symptom must stay away before the all-clear is due
    """

    def __init__(self, all_clear_after_s: float):
        self._all_clear_after_s = all_clear_after_s
        self._open = False
        self._clear_since: Optional[datetime] = None
        self._recurrences = 0

    def opens(self, now: datetime) -> bool:
        """
        The symptom is present.

        Args:
            now: The canonical time

        Returns:
            True when this opens the episode — the moment for the opening alert; False when the
            episode was already open, which counts as a recurrence
        """
        self._clear_since = None
        if self._open:
            self._recurrences += 1
            return False
        self._open = True
        self._recurrences = 0
        return True

    def clears(self, now: datetime) -> None:
        """
        The symptom went away; the all-clear waits for the quiet period.

        Args:
            now: The canonical time

        Returns:
            None
        """
        if self._open and self._clear_since is None:
            self._clear_since = now

    def all_clear_due(self, now: datetime) -> bool:
        """
        Whether the symptom has stayed away long enough — True once, and it closes the episode.

        Args:
            now: The canonical time

        Returns:
            True at the moment the all-clear should be sent
        """
        if not self._open or self._clear_since is None:
            return False
        if (now - self._clear_since).total_seconds() < self._all_clear_after_s:
            return False
        self._open = False
        self._clear_since = None
        return True

    def get_recurrences(self) -> int:
        """
        How often the symptom came back while the episode was open.

        Returns:
            The count since the episode opened
        """
        return self._recurrences

    def is_open(self) -> bool:
        """
        Whether the episode is open — its opening alert sent, its all-clear not yet.

        Returns:
            True while open
        """
        return self._open

    def get_all_clear_after_s(self) -> float:
        """
        The quiet period this episode's all-clear waits for.

        Returns:
            Seconds the symptom must stay away
        """
        return self._all_clear_after_s


def all_clear_detail(episode: AlertEpisode) -> str:
    """
    The detail line of an all-clear: how long it held, and whether it flapped meanwhile.

    Args:
        episode: The episode just closed — it holds the quiet period it waited for

    Returns:
        E.g. 'clear for 300 s · it came back 4 time(s) before that'
    """
    detail = f'clear for {episode.get_all_clear_after_s():.0f} s'
    recurrences = episode.get_recurrences()
    if recurrences:
        detail += f' · it came back {recurrences} time(s) before that'
    return detail
