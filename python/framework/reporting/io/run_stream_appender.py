"""
FiniexTestingIDE - Run Stream Appender

One stream of a run, appended a record at a time while the run goes — the hot half of the day
records #476 builds. The rules every such stream shares stand here once: the first line is a header
that names the stream, the version of its line shapes and its run; every record is one line on the
disk before the next is written (`JsonlStreamWriter`); and the first failure to write is said once
on the session's channel and ends the writing, because a line written after a broken one would turn
a readable cut-off tail into a damaged file. A file that cannot even be opened is the same failure,
said at the open, and the run goes on without the stream.

The state frames (#579) are written through it. The order-event stream, the tick stream record and
the acceptance studies' captures keep their own copies of these rules until they move here with #476.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter

# The value of `record_kind` on a stream's first line
STREAM_HEADER_KIND = 'header'


class RunStreamAppender:
    """
    Appends one run stream's records, one flushed line each, behind a header line.

    Args:
        path: The stream file; its directory is created
        stream: The stream's name, written into its header
        schema_version: The version of the stream's line shapes, written into its header
        run_id: The run the stream belongs to
        logger: Where a failure is said — the session's channel; None for nowhere
        stops: What the operator loses once the stream stops, one clause for the failure message
        header_fields: Further fields of the header line, such as a cadence
    """

    def __init__(
        self,
        path: Path,
        stream: str,
        schema_version: int,
        run_id: str,
        logger: Optional[AbstractLogger],
        stops: str,
        header_fields: Optional[Dict[str, Any]] = None,
    ):
        self._path = Path(path)
        self._stream = stream
        self._logger = logger
        self._stops = stops
        self._failed = False
        self._writer: Optional[JsonlStreamWriter] = None
        try:
            self._writer = JsonlStreamWriter(self._path)
        except OSError as e:
            self._report_failure(e)
            return
        self.write({
            'record_kind': STREAM_HEADER_KIND,
            'stream': stream,
            'schema_version': schema_version,
            'run_id': run_id,
            **(header_fields or {}),
        })

    def write(self, record: Dict[str, Any]) -> bool:
        """
        Append one record as one line; the first failure is said and ends the writing.

        Args:
            record: JSON-serializable values; None values are left out of the line

        Returns:
            True when the line was written
        """
        if self._writer is None or self._failed or not self._writer.is_open():
            return False
        try:
            self._writer.write(record)
            return True
        except OSError as e:
            self._report_failure(e)
            return False

    def close(self) -> None:
        """Close the stream. A failure here is said only when no earlier one was."""
        if self._writer is None:
            return
        try:
            self._writer.close()
        except OSError as e:
            # The close flushes what a failed write left in the buffer and fails again; that
            # one was said already, and the end of the session must not fail over it
            if not self._failed:
                self._report_failure(e)

    def is_writing(self) -> bool:
        """
        Whether records still reach the file.

        Returns:
            False once the stream failed or was closed
        """
        return self._writer is not None and not self._failed and self._writer.is_open()

    def get_path(self) -> Path:
        """
        The stream file.

        Returns:
            Its path
        """
        return self._path

    def _report_failure(self, error: OSError) -> None:
        """
        Say once that the stream cannot be written, and stop writing it.

        Args:
            error: What the file system answered
        """
        self._failed = True
        if self._logger is not None:
            self._logger.error(
                f'❌ The {self._stream} stream {self._path} could not be written: {error}. '
                f'{self._stops}; the session continues.')
