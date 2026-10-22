"""
FiniexTestingIDE - Start Failure Classifier

Whether a start that failed came from a passing cause (#598). A supervisor restarts a crash and
never a decision, and the exit code is how it tells them apart: a passing cause leaves with 75,
what the session decided keeps 2. Passing means an external system did not answer within its
budget — the connection ladder gave up after faults that were all transient, or a boot step that
turns a give-up into its own refusal said so (`TransientStartError`). A refused credential, an
unknown pair, a refusal by rule are not passing: a restart would only repeat them.

The ladder's give-up is raised while the last attempt's error is being handled, so that error is
the give-up's context; a boot step that wraps the give-up in an error of its own keeps it in the
chain, which is why the chain is walked.
"""

from typing import Iterator

from python.framework.exceptions.connection_errors import (
    ConnectionAttemptFailedError,
    ConnectionGaveUpError,
    TransientStartError,
)


def is_passing_start_failure(error: BaseException) -> bool:
    """
    Whether a failed start should be tried again by the supervisor.

    Args:
        error: The exception that ended the start

    Returns:
        True for a give-up after transient faults or a TransientStartError anywhere in the chain
    """
    for cause in _chain(error):
        if isinstance(cause, TransientStartError):
            return True
        if isinstance(cause, ConnectionGaveUpError):
            last = cause.__context__
            return isinstance(last, ConnectionAttemptFailedError) and not last.terminal
    return False


def _chain(error: BaseException) -> Iterator[BaseException]:
    """
    An exception and the ones it was raised from or while handling, each once.

    Args:
        error: The exception at the top

    Returns:
        The chain, top first
    """
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__
