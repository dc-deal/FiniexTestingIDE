"""
FiniexTestingIDE - Code Identity Errors
Exception types for a run whose code cannot be traced back to a commit (#551).
"""

from python.framework.exceptions.finiex_error import FiniexError


class UncommittedCodeError(FiniexError):
    """
    A session would send REAL orders from code that no commit describes.

    Raised at startup when the effective `dry_run` resolves to false and a repository the session
    loads code from is dirty, lies under no version control, or could not be read. Such a run
    cannot be traced back to the code that ran, and the parity backtest afterwards would compare
    against code that no longer exists. The message names every repository with its state and the
    two ways forward: commit, or start again with `--allow-dirty`, which records the override and
    reports it as a Tier-1 warning. A mock or dry-run session never raises it.
    """
    pass


class CodeChangedDuringStartupError(FiniexError):
    """
    A session would send REAL orders from code that changed while it was starting.

    The code identity is captured before the pipeline loads the components, and the pipeline then
    reads their files from disk again. Raised when a component's package no longer matches the
    digest recorded at the capture: the header's digests and patch would describe code the session
    did not load. `--allow-dirty` does not lift it — the patch it records is the one that is wrong.
    A mock or dry-run session logs a warning instead of raising it.
    """
    pass


class CodeIdentityCaptureError(FiniexError):
    """
    The code identity of a session could not be captured at all.

    Raised at the start of the session's startup handling, after the run header was written
    without a code identity, so the failure ends as a STARTUP FAILED with a record rather than as
    a stack trace before the session has one. The component resolution degrades by itself; what
    reaches this is an environment or storage fault around it — the patch store, a file that
    vanished between `git status` and its read. The message names the cause, and the full stack
    trace is in the session's global log.
    """
    pass
