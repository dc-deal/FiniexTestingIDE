"""
FiniexTestingIDE - Backtest Pass Kind Types (#460)

What a backtest instant did with the algo, for the drive that runs the steps after it.
"""

from enum import Enum


class BacktestPassKind(Enum):
    """
    What one instant of a backtest did with the algo.

    The drive decides from it what follows the instant: a state frame of a tick pass or of a
    heartbeat pass, the indicator series and the live export after a slice, nothing after an
    instant the algo did not run in.
    """
    TICK_PASS = 'tick_pass'      # a slice with an unclipped tick: the algo computed, once
    CLIPPED = 'clipped'          # a slice whose every tick was clipped: broker path and bars only
    GHOST_PASS = 'ghost_pass'    # a heartbeat logic's timer: the decision ran between ticks
    NO_PASS = 'no_pass'          # a due time alone, or a session end asked for in the delivery
