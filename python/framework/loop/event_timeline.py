"""
FiniexTestingIDE - Event Timeline (#457)

The loop's one timeline: events from every source, in the order the run's clock meets them.
"""

import heapq
from typing import List, Optional, Tuple

from python.framework.exceptions.loop_event_errors import PastLoopEventError
from python.framework.types.loop_types.event_kind_types import EventOrdering
from python.framework.types.loop_types.loop_event_types import LoopEvent


class EventTimeline:
    """
    The ordered merge of a run's events: earliest stamp first, then submission order.

    The stamp is the run's ordering stamp: `ts_event` in a backtest, `ts_init` in a live session
    (`EventOrdering`). No kind of event ever goes before another: events sharing a stamp form one
    instant, `pop_instant` hands it over whole, and the phases of `InstantPhase` order the work
    inside it. Time handed over never runs backwards: an event stamped before the last instant
    handed over is refused. An event stamped equal to it is taken, and forms a later instant at the
    same stamp — whether a dispatcher may meet that is #458's to settle. Nothing reads the wall
    clock here; the timeline only compares the stamps it was given.

    Args:
        ordering: Which stamp orders this run's events
    """

    def __init__(self, ordering: EventOrdering) -> None:
        self._ordering = ordering
        # (stamp, seq, push count, event): the push count keeps two entries that share stamp and
        # seq — two sequencers would be a fault upstream — from ever comparing events
        self._heap: List[Tuple[int, int, int, LoopEvent]] = []
        self._pushed = 0
        self._last_handed_stamp: Optional[int] = None

    def push(self, event: LoopEvent) -> None:
        """
        Place an event on the timeline.

        Args:
            event: The event; its stamps and seq are final
        """
        stamp = self._ordering.pick(event.ts_event, event.ts_init)
        if self._last_handed_stamp is not None and stamp < self._last_handed_stamp:
            raise PastLoopEventError(
                f'A {event.kind.value} event (seq {event.seq}) is stamped {stamp} by '
                f'{self._ordering.value}, before the instant {self._last_handed_stamp} the '
                f'timeline already handed over. Taking it would hand time backwards; the fault is '
                f'where the event was stamped.')
        heapq.heappush(self._heap, (stamp, event.seq, self._pushed, event))
        self._pushed += 1

    def peek(self) -> Optional[LoopEvent]:
        """
        The next event, left in place.

        Returns:
            The earliest event, or None when the timeline is empty
        """
        return self._heap[0][3] if self._heap else None

    def pop(self) -> Optional[LoopEvent]:
        """
        Take the next event.

        Returns:
            The earliest event, or None when the timeline is empty
        """
        if not self._heap:
            return None
        stamp, _, _, event = heapq.heappop(self._heap)
        self._last_handed_stamp = stamp
        return event

    def pop_instant(self) -> Tuple[LoopEvent, ...]:
        """
        Take every event of the next instant: all that share the earliest stamp.

        Returns:
            The instant's events in submission order, or an empty tuple when the timeline is empty
        """
        if not self._heap:
            return ()
        stamp = self._heap[0][0]
        instant: List[LoopEvent] = []
        while self._heap and self._heap[0][0] == stamp:
            instant.append(heapq.heappop(self._heap)[3])
        self._last_handed_stamp = stamp
        return tuple(instant)

    def get_ordering(self) -> EventOrdering:
        """
        Which stamp orders this timeline.

        Returns:
            The ordering it was built with
        """
        return self._ordering

    def __len__(self) -> int:
        return len(self._heap)
