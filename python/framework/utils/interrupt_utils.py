"""
FiniexTestingIDE - Interrupt Utils
SIGTERM handled like Ctrl+C for the duration of a block.

A process ended by SIGTERM's default action stops at once: no finally block runs. An import or a
bar render needs its finally — it rebuilds the bar index from the files that are there — so for
as long as such a block runs, SIGTERM (a `kill`, a debugger's stop) raises KeyboardInterrupt
instead, and the code ends through the same path as Ctrl+C.
"""

import signal
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def sigterm_as_interrupt() -> Iterator[None]:
    """
    Raise KeyboardInterrupt on SIGTERM inside the block; the previous handler returns after it.

    Only the main thread may install a signal handler, and only it receives the exception.

    Returns:
        Nothing — a context manager
    """
    previous = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _raise_interrupt(signum: int, frame: object) -> None:
    """
    The SIGTERM handler while the block runs.

    Args:
        signum: The signal number
        frame: The interrupted frame

    Returns:
        None
    """
    raise KeyboardInterrupt(f'signal {signum}')
