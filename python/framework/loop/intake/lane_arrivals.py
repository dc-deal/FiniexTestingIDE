"""
FiniexTestingIDE - Lane Arrivals (#461)

What tells the AutoTrader's intake that something waits on one of its lanes, and since when.
"""

import threading
import time
from typing import Optional, Tuple


class LaneArrivals:
    """
    One intake lane's arrivals, as the intake sees them: whether something waits, the earliest
    receipt among it, and how many arrived.

    What arrived stays where its producer put it — the ticks on the tick queue, the venue's answers
    in the executor's inbox — and is read only on the loop's thread; this records the arrival and
    nothing else, so it can never drop or merge one (#461, *Two intake lanes*). Each receipt is
    kept with its monotonic companion, so the intake measures a wait or a lag on the monotonic
    clock while it orders by the receipt.
    """

    def __init__(self):
        """Start with nothing waiting."""
        self._lock = threading.Lock()
        self._earliest: Optional[Tuple[int, float]] = None
        self._count = 0

    def note(self, receipt_msc: int) -> None:
        """
        Record an arrival. Called on the producer's thread right after it put what arrived.

        Args:
            receipt_msc: When it arrived, on the receipt axis (UTC epoch milliseconds)
        """
        monotonic_s = time.monotonic()
        with self._lock:
            if self._earliest is None or receipt_msc < self._earliest[0]:
                self._earliest = (receipt_msc, monotonic_s)
            self._count += 1

    def take(self) -> Optional[Tuple[int, float, int]]:
        """
        Hand over what arrived since the last call, and start again.

        Returns:
            (earliest receipt in ms, its monotonic companion in seconds, how many arrived), or
            None when nothing arrived
        """
        with self._lock:
            if self._earliest is None:
                return None
            taken = (self._earliest[0], self._earliest[1], self._count)
            self._earliest = None
            self._count = 0
            return taken
