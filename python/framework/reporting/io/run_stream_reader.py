"""
FiniexTestingIDE - Run Stream Reader

The two ends of a run stream, read without its middle (#611). A stream a session appends to for
thirty days holds tens of megabytes, and a route asked every day — and polled by a viewer while a
window is open — needs only its header, its first record and its latest one. So the file is read
from its start to its second line and from its end back to its last complete line, and nothing in
between.

A last line without its newline that does not parse is a line cut off mid-write: it is left out and
reported, and the record before it is the latest one — the rule the order-event reader follows. A
complete line that does not parse, a line that is not a JSON object and a byte that is not UTF-8
are a damaged file: the writer ends every record with a newline and escapes everything outside
ASCII, so none of them can be a line cut off.

When #476 seals a stream per trading day, its day records take this read over and the callers do
not change.
"""

import json
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Tuple

from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.run_stream_appender import STREAM_HEADER_KIND
from python.framework.types.run_stream_types import RunStreamEnds

# How much one step back from the end reads: a record is a few kilobytes, so one step usually holds
# the last lines
_TAIL_BLOCK_BYTES = 64 * 1024


def read_stream_ends(path: Path, block_bytes: int = _TAIL_BLOCK_BYTES) -> RunStreamEnds:
    """
    Read a run stream's header, its first record and its last complete record.

    Args:
        path: The stream's file
        block_bytes: How much one step back from the end reads

    Returns:
        The stream's two ends; an empty file holds none of them
    """
    path = Path(path)
    with open(path, 'rb') as handle:
        size = handle.seek(0, 2)
        if size == 0:
            return RunStreamEnds(header=None, first=None, last=None, truncated_tail=False)
        handle.seek(0)
        header, first = _read_head(handle, path)
        last, truncated = _read_tail(handle, size, block_bytes, path)
    if last is not None and _is_header(last):
        last = None
    return RunStreamEnds(header=header, first=first, last=last, truncated_tail=truncated)


def _read_head(handle: BinaryIO,
               path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    The header and the first record, read from the start of the file.

    Args:
        handle: The open file, at its start
        path: The file, for the error a damaged line raises

    Returns:
        (the header or None when the first line is none, the first record or None)
    """
    line = handle.readline()
    record = _parse(line, path, complete=line.endswith(b'\n'), where='the first line')
    if record is None or not _is_header(record):
        return None, record
    line = handle.readline()
    if not line:
        return record, None
    return record, _parse(line, path, complete=line.endswith(b'\n'), where='the second line')


def _read_tail(handle: BinaryIO, size: int, block_bytes: int,
               path: Path) -> Tuple[Optional[Dict[str, Any]], bool]:
    """
    The last record, read backwards from the end in blocks until one whole line is in hand.

    Args:
        handle: The open file
        size: The file's length in bytes
        block_bytes: How much one step back reads
        path: The file, for the error a damaged line raises

    Returns:
        (the last record or None, whether a last line cut off mid-write was left out)
    """
    position = size
    buffer = b''
    while True:
        pieces = buffer.split(b'\n')
        # The first piece starts before what was read unless the read reached the file's start
        whole = pieces[:-1] if position == 0 else pieces[1:-1]
        if position == 0 or any(line.strip() for line in whole):
            break
        step = min(block_bytes, position)
        position -= step
        handle.seek(position)
        buffer = handle.read(step) + buffer
    # The piece after the last newline: empty when the file ends on one, else a line cut off or a
    # record whose newline was not written
    trailing = pieces[-1]
    truncated = False
    if trailing.strip():
        record = _parse(trailing, path, complete=False, where='the last line')
        if record is not None:
            return record, False
        truncated = True
    for line in reversed(whole):
        if line.strip():
            return _parse(line, path, complete=True, where='the last complete line'), truncated
    return None, truncated


def _parse(line: bytes, path: Path, complete: bool, where: str) -> Optional[Dict[str, Any]]:
    """
    One line as a record.

    Args:
        line: The line's bytes, with or without its newline
        path: The file, for the error a damaged line raises
        complete: Whether the line ended with its newline; only a line that did not may be cut off
        where: Which line it is, for the error

    Returns:
        The record, or None for an empty line or a line cut off mid-write
    """
    try:
        text = line.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ReportArtifactUnreadableError(path.name, str(path), f'{where}: not UTF-8: {e}') from e
    if not text.strip():
        return None
    try:
        record = json.loads(text)
    except json.JSONDecodeError as e:
        if not complete:
            return None
        raise ReportArtifactUnreadableError(path.name, str(path), f'{where}: {e}') from e
    if not isinstance(record, dict):
        raise ReportArtifactUnreadableError(path.name, str(path), f'{where}: not a JSON object')
    return record


def _is_header(record: Dict[str, Any]) -> bool:
    """
    Whether a record is a stream's header line.

    Args:
        record: A parsed line

    Returns:
        True for the header the appender writes first
    """
    return record.get('record_kind') == STREAM_HEADER_KIND
