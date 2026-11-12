"""
FiniexTestingIDE - Loop Event Kinds (#457)

What can happen on the loop's one timeline, and which of an event's two stamps orders it.
"""

from enum import Enum
from typing import TypeVar

_Stamp = TypeVar('_Stamp')


class EventKind(Enum):
    """
    The kind of a loop event: one per source that feeds the timeline.

    The kind decides which work an event brings to its instant. It never decides which of two
    events goes first: events sharing a stamp form one instant, and the phases of `InstantPhase`
    order the work inside it, whatever kinds arrived.
    """
    DATA_SLICE = 'data_slice'     # every tick of one instant
    TIME = 'time'                 # a timer or heartbeat falls due, with or without data beside it
    RESOLUTION = 'resolution'     # a pending order at the broker comes due
    SIGNAL = 'signal'             # an external signal snapshot becomes visible


class EventOrdering(Enum):
    """
    Which of an event's two stamps orders the timeline.

    A backtest replays recorded time: it orders by the stamp each event carries from its source
    (`ts_event`), and its clock follows that stamp. A live session cannot order by clocks it does
    not own without inserting an event before one it has already processed, so it orders by its
    own receipt stamp (`ts_init`). That holds only while the receipt stamps a live session gives
    its events never decrease in the order the events arrive — the live intake's to guarantee
    (#461). Both stamps travel on every event either way, and the one not ordering is kept as data.
    """
    BY_TS_EVENT = 'ts_event'      # a backtest
    BY_TS_INIT = 'ts_init'        # a live session

    def pick(self, ts_event: _Stamp, ts_init: _Stamp) -> _Stamp:
        """
        Choose, of a pair, the one this ordering goes by — the one place that says which.

        The pair can be the two stamps of an event or the two functions that read them from a
        tick, so a caller resolves its choice once rather than on every item.

        Args:
            ts_event: The counterpart of the source stamp
            ts_init: The counterpart of the receipt stamp

        Returns:
            `ts_event` when ordering by the source stamp, `ts_init` when ordering by receipt
        """
        if self is EventOrdering.BY_TS_EVENT:
            return ts_event
        if self is EventOrdering.BY_TS_INIT:
            return ts_init
        raise ValueError(f'No stamp is declared for the ordering {self.name}')
