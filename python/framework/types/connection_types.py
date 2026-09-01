"""
FiniexTestingIDE - External Connection Types
How a failed external connection is classified (#473).

Every connection this system holds must answer the same question when the other side does
not respond, and it must answer it the same way: wait and try again, stop and tell a human,
or refuse to run at all. The classification lives here so that the next connection inherits
a decision instead of inventing a fourth behaviour.
"""

from enum import Enum


class ConnectionOutcome(Enum):
    """
    What a connection failure means for the caller.

    TRANSIENT: the other side is briefly unavailable — their proxy cycling, a dropped
        socket, a 5xx, a DNS blip. Wait, escalate the delay, try again.
    TERMINAL: retrying cannot fix it — a refused credential, an unusable cursor, an
        unknown pipeline id. Stop and ALERT; retrying a typo forever reports their
        outage for our mistake.
    INADMISSIBLE: the run cannot legitimately start or continue without this — warmup
        bars that never arrived, an account balance that could not be read. There is no
        staleness contract to degrade into.
    """
    TRANSIENT = 'transient'
    TERMINAL = 'terminal'
    INADMISSIBLE = 'inadmissible'


class GiveUpAction(Enum):
    """
    What a caller does once its ladder has stopped trying.

    ABORT: raise ConnectionGaveUpError — the caller cannot proceed without this input.
    DEGRADE: carry on with a reduced input and say so loudly. Only legitimate where a
        contract already describes the reduced state (the staleness contracts #434/#436,
        or a cached broker config that reports its own age).
    ALERT: carry on unchanged and report. For inputs that are observational rather than
        load-bearing — the producer identity probe is the case.
    """
    ABORT = 'abort'
    DEGRADE = 'degrade'
    ALERT = 'alert'
