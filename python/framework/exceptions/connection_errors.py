"""
FiniexTestingIDE - External Connection Errors
Exception types for the shared connection retry ladder (#473).
"""

from python.framework.exceptions.finiex_error import FiniexError


class ConnectionAttemptFailedError(FiniexError, RuntimeError):
    """
    One attempt against an external connection did not succeed.

    For the readers that report failure as a RESULT rather than by raising — the producer
    registry and the health probe both answer with an `ok` flag plus a classified detail.
    Their call site raises this so the same ladder serves them, and carries the one thing
    only that reader knows: whether trying again could help.

    Args:
        message: What did not work, in the reader's own words
        terminal: True when a retry cannot fix it (a refused credential, an unknown id)
    """

    def __init__(self, message: str, terminal: bool = False):
        super().__init__(message)
        self.terminal = terminal


class ConnectionGaveUpError(FiniexError, RuntimeError):
    """
    An external connection stopped trying and the caller must not continue.

    Raised when a ladder reaches a TERMINAL classification, or exhausts its
    attempt budget on a connection configured to abort. Never raised for a
    connection configured to degrade — that caller carries on with a reduced
    input and says so.
    """
    pass


class ConnectionInadmissibleError(FiniexError, RuntimeError):
    """
    A precondition an external connection must satisfy cannot be satisfied.

    The session refuses to start. Distinct from ConnectionGaveUpError: that one
    means the other side did not answer, this one means starting would produce a
    wrong run — warmup bars that never arrived, an account balance that could not
    be read. There is no staleness contract for either, so there is nothing to
    degrade into.
    """
    pass
