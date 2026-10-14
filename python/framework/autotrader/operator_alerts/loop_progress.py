"""
FiniexTestingIDE - Loop Progress

When the tick loop last came round — the one fact the liveness ping vouches for (#235). A ping
sent because the PROCESS is alive would report a hung loop as healthy, which is the one failure a
supervisor cannot see either: the process neither crashes nor exits. So the loop stamps this on
every pass, a tick or an idle heartbeat alike, and the pinger reads it from its own thread.

A duration on the monotonic clock (§9), never a wall-clock difference. One float written by one
thread and read by another — an atomic store in CPython, so no lock is taken on the tick path.
"""

import time
from typing import Callable, Optional


class LoopProgress:
    """
    The tick loop's most recent pass.

    Args:
        clock: The monotonic clock — injected by tests
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._clock = clock if clock is not None else time.monotonic
        self._last_pass: Optional[float] = None

    def stamp(self) -> None:
        """
        Record that the loop has come round now.

        Returns:
            None
        """
        self._last_pass = self._clock()

    def seconds_since_last_pass(self) -> Optional[float]:
        """
        How long ago the loop last came round.

        Returns:
            Seconds since the last pass, None before the first one
        """
        last_pass = self._last_pass
        if last_pass is None:
            return None
        return self._clock() - last_pass
