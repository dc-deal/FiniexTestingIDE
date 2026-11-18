"""
FiniexTestingIDE - Loop Event Kinds (#457)

What can happen on the loop's one timeline.
"""

from enum import Enum


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

