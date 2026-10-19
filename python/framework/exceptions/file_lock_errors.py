"""
FiniexTestingIDE - File Lock Errors
"""

from pathlib import Path

from python.framework.exceptions.finiex_error import FiniexError


class FileLockHeldError(FiniexError):
    """
    Raised when a file lock is already held by another open file.

    Args:
        path: The lock file
    """

    def __init__(self, path: Path):
        self.path = path
        super().__init__(f'The lock on {path} is held by another process')
