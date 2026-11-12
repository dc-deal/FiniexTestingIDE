"""
FiniexTestingIDE - Loop Event Errors (#457)

PastLoopEventError: an event reached the loop's timeline stamped earlier than an instant the
timeline had already handed over. Taking it would hand time backwards to whatever runs the
instants, so the timeline refuses it; the producer that stamped it is where the fault is.
"""

from python.framework.exceptions.finiex_error import FiniexError


class PastLoopEventError(FiniexError, ValueError):
    """
    An event stamped before the last instant the timeline handed over.

    Inherits ValueError: the event is refused as an argument, and nothing on the timeline changed.
    """
    pass
