"""
FiniexTestingIDE - JSON-Lines Stream Writer

One JSON object per line, handed to the operating system as it is written, so a file being written
is readable while it grows and a process that dies loses at most the line it was writing. Shared by
the run's streams that are written as they happen, such as the field study's record and the
order-event stream (#362).

The lines go straight to the file descriptor, with no buffer of our own in between (#632): a write
that fails leaves nothing behind to be flushed again at the close, and the writer knows to the byte
where the last complete line ends — what a stream that keeps its unwritten lines needs to cut off a
line half on the disk before it writes it again.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from python.framework.utils.file_utils import create_directory


def jsonl_line(record: Dict[str, Any]) -> str:
    """
    One record as the line a JSON-lines stream holds: its None values left out, its newline on.

    Args:
        record: JSON-serializable values

    Returns:
        The line
    """
    return json.dumps({key: value for key, value in record.items() if value is not None}) + '\n'


class JsonlStreamWriter:
    """
    Append records to a JSON-lines file, one line on the disk's way each.

    `None` values are left out of a line: a reader treats a missing key as absent, and a line
    of nulls costs disk on every record of a long session.

    Args:
        path: The file to write; its directory is created
        append: Continue an existing file instead of starting it afresh
    """

    def __init__(self, path: Path, append: bool = False):
        self._path = Path(path)
        create_directory(self._path.parent, holds=f'the stream {self._path.name}')
        # Always appending, also for a fresh file: after a cut the next line lands at the new end
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | (0 if append else os.O_TRUNC)
        self._fd: Optional[int] = os.open(self._path, flags, 0o666)
        # Where the last complete line ends — a line a failed write left half on the disk lies past it
        self._size = os.fstat(self._fd).st_size

    def write(self, record: Dict[str, Any]) -> None:
        """
        Write one record as one line.

        Args:
            record: JSON-serializable values; None values are dropped
        """
        self.write_line(jsonl_line(record))

    def write_line(self, line: str) -> None:
        """
        Write one line as it stands, all of its bytes or an error.

        Args:
            line: The line, its newline on
        """
        if self._fd is None:
            return
        data = line.encode('utf-8')
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]
        self._size += len(data)

    def sync(self) -> None:
        """
        Force what was written to the disk.
        """
        if self._fd is not None:
            os.fsync(self._fd)

    def cut_to(self, size: int) -> None:
        """
        Cut the file back to a length where a complete line ends; what lay past it is gone.

        Args:
            size: The length to keep, in bytes
        """
        if self._fd is None:
            return
        on_disk = os.fstat(self._fd).st_size
        # Only ever shorter: a file someone cut below it is not filled up with zeros
        if on_disk > size:
            os.ftruncate(self._fd, size)
        self._size = min(size, on_disk)

    def get_size(self) -> int:
        """
        Where the last complete line this writer knows of ends.

        Returns:
            The length in bytes; a line a failed write left half on the disk is not counted
        """
        return self._size

    def close(self) -> None:
        """Close the file. Writing after this does nothing."""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        os.close(fd)

    def is_open(self) -> bool:
        """
        Whether records are still being written.

        Returns:
            True until close() was called
        """
        return self._fd is not None

    def get_path(self) -> Path:
        """
        The file being written.

        Returns:
            Its path
        """
        return self._path
