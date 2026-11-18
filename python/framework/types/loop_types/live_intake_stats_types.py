"""
FiniexTestingIDE - Live Intake Stats (#461)

What the AutoTrader's intake measured while it handed instants to the loop.
"""

from dataclasses import dataclass


@dataclass
class LiveIntakeStats:
    """
    What the AutoTrader's intake counted and measured over a session.

    Live, lateness is a measurement, never an input: the intake coalesces a backlog of ticks into
    one slice and says how often and how large (#461, *Clipping stays a MEASUREMENT in live*); how
    late, and how many ticks the algo did not compute on, the clipping monitor counts, pass by pass.

    Args:
        instants: How many instants it handed over
        coalesced_slices: Slices holding more than one arrival — live only: a backlog the algo
            computed on once, on the newest; a replay hands each arrival as a slice of its own
        max_slice_ticks: The most ticks one slice held
        reordered_stamps: Arrivals stamped before the instant already handed over, which joined
            the running instant — a normal reordering between two threads, not a fault
        execution_arrivals: The venue's answers that arrived on the execution lane
        timers_fired: Timers handed over, the heartbeat included
    """
    instants: int = 0
    coalesced_slices: int = 0
    max_slice_ticks: int = 0
    reordered_stamps: int = 0
    execution_arrivals: int = 0
    timers_fired: int = 0
