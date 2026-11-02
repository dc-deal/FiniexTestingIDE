"""
FiniexTestingIDE - Stop Budget (#630)

What a session's stop may still spend before its supervisor kills the process. A supervisor sends
a signal and kills after a period it never tells the process, so the operator declares that period
and the steps of the stop ask this unit before they start something that takes time.

It answers and enforces nothing: what a step does with the answer is the step's. With no kill
declared every answer says there is time, and each wait keeps its own bound.
"""

import time
from typing import Optional


class StopBudget:
    """
    The time a stop has left, on the monotonic clock from the instant the stop was asked for.

    Args:
        supervisor_kill_after_s: The time the supervisor gives the process after its signal, None
            when it is not declared
        reserve_for_records_s: What a declared kill keeps free for the reports and the ledger row
        asked_at_monotonic_s: `time.monotonic()` at the instant the stop was asked for
    """

    def __init__(
        self,
        supervisor_kill_after_s: Optional[float],
        reserve_for_records_s: float,
        asked_at_monotonic_s: float,
    ):
        self._kill_after_s = supervisor_kill_after_s
        self._reserve_s = reserve_for_records_s
        self._asked_at_s = asked_at_monotonic_s

    def elapsed_s(self) -> float:
        """
        How long the stop has been going on.

        Returns:
            Seconds since the stop was asked for
        """
        return time.monotonic() - self._asked_at_s

    def until_kill_s(self) -> Optional[float]:
        """
        How long until the declared kill.

        Returns:
            Seconds until it, 0.0 once it has passed; None when no kill is declared
        """
        if self._kill_after_s is None:
            return None
        return max(0.0, self._kill_after_s - self.elapsed_s())

    def has_time_for(self, seconds: float) -> bool:
        """
        Whether a step of this length still fits in front of the reserve for the records.

        Args:
            seconds: How long the step can take at most

        Returns:
            True when it fits, or when no kill is declared
        """
        until_kill_s = self.until_kill_s()
        if until_kill_s is None:
            return True
        return until_kill_s - self._reserve_s >= seconds

    def order_wait_bound_s(self, wanted_s: float, still_to_come_s: float) -> float:
        """
        How much longer, from now, the wait for the orders on their way may run.

        Measured from NOW, because what the kill leaves shrinks while the wait runs: a caller that
        re-reads it on each pass hands in what it still wants, its bound less what it has waited.
        Handing in the whole bound on every pass would count the time already waited twice.

        Args:
            wanted_s: How much longer the wait wants to run
            still_to_come_s: What the stop has yet to spend after the wait, the reserve aside

        Returns:
            `wanted_s` when no kill is declared; otherwise the smaller of `wanted_s` and what the
            kill leaves after `still_to_come_s` and the reserve. Never below zero
        """
        until_kill_s = self.until_kill_s()
        if until_kill_s is None:
            return max(0.0, wanted_s)
        return max(0.0, min(wanted_s, until_kill_s - self._reserve_s - still_to_come_s))
