"""
FiniexTestingIDE - File Lock
A kernel lock on a file: held while its holder lives, released the moment it dies.

The lock is an `flock` on an open file — not a file that exists and not a process id that is
alive. A file outlives a killed process, and a process id is reused. The kernel releases the lock
with the last open descriptor of its holder: a normal exit, an exception, `kill -9`. Measured on
2026-10-14 (#598): it holds on this container's bridged disk as on its own, it takes 2.6 ms there
against 0.02 ms, and it is free again at once after `kill -9`.

A child the holder FORKS shares the open descriptor and with it the one lock. If the holder dies,
the lock stays held until every such child is gone; if the holder releases it, it is free for all
of them at once. A program the holder EXECS shares nothing: the file is opened close-on-exec.

The file is readable by its owner alone, and so is its note — who holds it — which a refused
process reads for its message and never decides on.
"""

import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import Optional, Self, Type

from python.framework.exceptions.file_lock_errors import FileLockHeldError


class FileLock:
    """
    An exclusive lock on a file, held until it is released or its holder ends.
    """

    def __init__(self, path: Path):
        """
        Open the file, creating it when missing, and take the lock without waiting.

        Args:
            path: The lock file; its directory is created when missing
        """
        self._path = path
        self._descriptor: Optional[int] = None
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise FileLockHeldError(path)
        except OSError:
            # A mount without locks (ENOLCK, EOPNOTSUPP): the descriptor must not outlive the try
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def get_path(self) -> Path:
        """
        The lock file.

        Returns:
            The path the lock was taken on
        """
        return self._path

    def write_note(self, text: str) -> None:
        """
        Replace what the lock file says about its holder. Does nothing once released.

        Args:
            text: The note, written as UTF-8

        Returns:
            None
        """
        if self._descriptor is None:
            return
        os.ftruncate(self._descriptor, 0)
        os.pwrite(self._descriptor, text.encode('utf-8'), 0)

    def release(self) -> None:
        """
        Give the lock up and close the file. A second call does nothing.

        Returns:
            None
        """
        if self._descriptor is None:
            return
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            self._descriptor = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc: Optional[BaseException],
                 traceback: Optional[TracebackType]) -> None:
        self.release()


def read_lock_note(path: Path) -> str:
    """
    What a lock file says about its holder, read without taking the lock.

    Args:
        path: The lock file

    Returns:
        The note, or '' when there is none or it cannot be read
    """
    try:
        return path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return ''


def is_lock_held(path: Path) -> bool:
    """
    Whether another open file holds the lock on a path, asked without waiting and without holding.

    The question is asked with a SHARED lock, so two readers asking at once do not mistake each
    other for a holder; an exclusive holder refuses it. A missing file is never created.

    Args:
        path: The lock file

    Returns:
        True while a holder lives; False when the file is missing or nobody holds it
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)
