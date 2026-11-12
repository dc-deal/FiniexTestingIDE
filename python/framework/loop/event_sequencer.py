"""
FiniexTestingIDE - Event Sequencer (#457)

Hands out the submission order of a run's loop events.
"""


class EventSequencer:
    """
    The run's counter for `LoopEvent.seq`: one per run, counting from 0 in submission order.

    The timeline orders by stamp, then by this number, so two events sharing a stamp leave in the
    order they were submitted — a fill sent first resolves first, as a FIX sequence number would
    have it. It is handed out when an event is made rather than when it is queued, so an event is
    complete and immutable from the moment it exists.
    """

    def __init__(self) -> None:
        self._next_seq = 0

    def next_seq(self) -> int:
        """
        Take the next submission number.

        Returns:
            The number, one more than the last one handed out
        """
        seq = self._next_seq
        self._next_seq += 1
        return seq
