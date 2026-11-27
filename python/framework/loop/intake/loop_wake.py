"""
FiniexTestingIDE - Loop Wake (#461)

What lets a thread other than the tick source tell the AutoTrader's loop that something arrived,
so the loop drains on arrival rather than on the next tick or heartbeat.
"""

import queue


class LoopWake:
    """
    The loop's wake: a marker put on the tick queue the loop blocks on.

    One blocking primitive for both lanes (#461, *Wake-on-arrival*: "a unified wake-queue"). Every
    arrival a source puts wakes the loop by itself; a producer on the other lane — the venue's answers
    landing in the executor's inbox — wakes it by putting this marker. It only says "something
    arrived": what arrived the intake reads from the lanes, on the loop's thread, so no state is
    ever mutated off it (#360, #320), and no answer travels through the tick queue.
    """

    def __init__(self, lane: queue.Queue):
        """
        Args:
            lane: The queue the loop blocks on — the tick queue
        """
        self._lane = lane

    def notify(self) -> None:
        """Wake the loop. Safe from any thread."""
        self._lane.put(self)

    def is_wake(self, item: object) -> bool:
        """
        Whether an item taken off the lane is a wake rather than an arrival or the lane's end.

        Args:
            item: What the lane handed out

        Returns:
            True for the marker
        """
        return item is self
