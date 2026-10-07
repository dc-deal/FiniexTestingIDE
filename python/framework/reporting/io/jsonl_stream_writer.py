"""
FiniexTestingIDE - JSON-Lines Stream Writer

One JSON object per line, flushed after every line, so a file being written is readable while it
grows and a process that dies loses at most the line it was writing. Shared by the run's streams
that are written as they happen — the field study's record and the order-event stream (#362) —
and by the day records of #476 when they come.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional, TextIO


class JsonlStreamWriter:
    """
    Append records to a JSON-lines file, one flushed line each.

    `None` values are left out of a line: a reader treats a missing key as absent, and a line
    of nulls costs disk on every record of a long session.

    Args:
        path: The file to write; its directory is created
        append: Continue an existing file instead of starting it afresh
    """

    def __init__(self, path: Path, append: bool = False):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: Optional[TextIO] = open(
            self._path, 'a' if append else 'w', encoding='utf-8')

    def write(self, record: Dict[str, Any]) -> None:
        """
        Write one record as one line and flush it.

        Args:
            record: JSON-serializable values; None values are dropped
        """
        if self._handle is None:
            return
        compact = {key: value for key, value in record.items() if value is not None}
        self._handle.write(json.dumps(compact) + '\n')
        self._handle.flush()

    def close(self) -> None:
        """Close the file. Writing after this does nothing."""
        if self._handle is None:
            return
        self._handle.close()
        self._handle = None

    def is_open(self) -> bool:
        """
        Whether records are still being written.

        Returns:
            True until close() was called
        """
        return self._handle is not None

    def get_path(self) -> Path:
        """
        The file being written.

        Returns:
            Its path
        """
        return self._path
