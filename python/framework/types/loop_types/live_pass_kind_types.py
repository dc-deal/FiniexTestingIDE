"""
FiniexTestingIDE - Live Pass Kinds (#461)

What an instant of an AutoTrader session did with the algo.
"""

from enum import Enum


class LivePassKind(Enum):
    """
    What an instant of an AutoTrader session did with the algo — what decides which records the
    session's drive writes after it.
    """
    # The algo computed on the slice's newest tick
    TICK_PASS = 'tick_pass'
    # A heartbeat logic's decision ran between ticks
    GHOST_PASS = 'ghost_pass'
    # Neither: a timer's cadence work, the venue's answers resolving, or a session end a hook
    # asked for before the algo
    NO_PASS = 'no_pass'
