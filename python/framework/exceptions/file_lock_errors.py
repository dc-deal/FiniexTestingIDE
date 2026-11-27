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
        note: What the holder wrote about itself; '' when it wrote nothing (a shared holder never does)
        waited_s: How long the request kept asking; 0 for one that refused at once
    """

    def __init__(self, path: Path, note: str = '', waited_s: float = 0.0):
        self.path = path
        self.note = note
        self.waited_s = waited_s
        holder = f' ({note})' if note else ''
        waited = f'; waited {waited_s:g} s' if waited_s > 0 else ''
        super().__init__(f'The lock on {path} is held by another process{holder}{waited}')
