"""
FiniexTestingIDE - Timer Kinds (#461)

What a timer on the loop's timeline is for.
"""

from enum import Enum


class TimerKind(Enum):
    """
    What a timer on the loop's timeline is for — its work, not its schedule.

    A backtest's timeline makes only heartbeats (#460). The AutoTrader's makes all of them (#461),
    each on its own schedule, so the cadence work runs when it is due rather than on whichever
    pass happens to come next.
    """
    # A pass between data instants after a quiet heartbeat interval: the executor's heartbeat,
    # the staleness check, a heartbeat logic's decision, the display pulse
    HEARTBEAT = 'heartbeat'
    # The venue is asked for its view of the account and the orders (#349)
    RECONCILE = 'reconcile'
    # The algo's state is written for a restart (#354)
    PERSISTENCE = 'persistence'
    # A period of the state frames' cadence begins (#579)
    STATE_FRAME_SLOT = 'state_frame_slot'
    # The free space under the session's records is read (#632)
    DISK_READING = 'disk_reading'
