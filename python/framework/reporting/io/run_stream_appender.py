"""
FiniexTestingIDE - Run Stream Appender

One stream of a run, appended a record at a time while the run goes — the hot half of the day
records #476 builds. The rules every such stream shares stand here once: the first line is a header
that names the stream, the version of its line shapes and its run; every record is one line on the
disk before the next is written (`JsonlStreamWriter`); and the first failure to write is said once
on the session's channel and ends the writing, because a line written after a broken one would turn
a readable cut-off tail into a damaged file. A file that cannot even be opened is the same failure,
said at the open, and the run goes on without the stream — unless the stream is required: then the
failure ends the start that opens it.

At a trading-day boundary a session seals the day (#476): the file is closed, its lines move into
the day record (`run_stream_day_record`), and the file starts afresh with a header that names the
next day. Writing, sealing and closing take one lock, so a writer on another thread — the tick
stream's socket loop — never writes into the middle of a seal.

The state frames (#579), the tick stream record (#609), the trades, the diagnostics rows (#476) and
the order events (#362) are written through it. The acceptance studies' captures keep their own
copy of these rules.
"""

import threading
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from python.framework.constants.run_layout_constants import (
    STREAM_HEADER_KIND,
    STREAM_TRADING_DAY_FIELD,
)
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter, jsonl_line
from python.framework.reporting.io.run_stream_day_record import seal_stream_day
from python.framework.types.run_stream_types import DayRecordSummary
from python.framework.utils.atomic_file_utils import write_text_atomic


def stream_header(stream: str, schema_version: int, run_id: str,
                  fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    A run stream's first line: what it is, the version of its line shapes and the run it belongs
    to — the same for every stream, whoever writes it.

    Args:
        stream: The stream's name
        schema_version: The version of its line shapes
        run_id: The run it belongs to
        fields: Further fields of the stream's header, such as a cadence

    Returns:
        The header record
    """
    return {
        'record_kind': STREAM_HEADER_KIND,
        'stream': stream,
        'schema_version': schema_version,
        'run_id': run_id,
        **(fields or {}),
    }


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
        time_fields: The fields a line takes its time from when its day is sealed, the first one
            it carries
        trading_day: The trading day the file holds, written into its header; None when the
            stream is never sealed, or before a session sealed its first day
        required: An open that fails raises instead of being said — for a stream the run must not
            go on without
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
        time_fields: Tuple[str, ...] = (),
        trading_day: Optional[date] = None,
        required: bool = False,
    ):
        self._path = Path(path)
        self._stream = stream
        self._logger = logger
        self._stops = stops
        self._time_fields = tuple(time_fields)
        self._header_base = stream_header(stream, schema_version, run_id, header_fields)
        self._trading_day = trading_day
        self._failed = False
        self._lock = threading.Lock()
        self._writer: Optional[JsonlStreamWriter] = None
        try:
            self._writer = JsonlStreamWriter(self._path)
            self._writer.write(self._header())
        except OSError as e:
            if required:
                if self._writer is not None:
                    self._writer.close()
                raise
            self._report_failure(e)

    def write(self, record: Dict[str, Any], at: Optional[str] = None) -> bool:
        """
        Append one record as one line; the first failure is said and ends the writing.

        Args:
            record: JSON-serializable values; None values are left out of the line
            at: Which record it is, named in the failure message — such as its sequence number

        Returns:
            True when the line was written
        """
        with self._lock:
            if not self._writing():
                return False
            try:
                self._writer.write(record)
                return True
            except OSError as e:
                self._report_failure(e, at)
                return False

    def seal_day(self, day_path_for: Callable[[date], Path], sealed_day: date,
                 next_day: date) -> Optional[DayRecordSummary]:
        """
        Seal the trading day the file holds into its day record and start the file afresh for the
        next day (#476). A stream that stopped writing is not sealed. A day that cannot be sealed,
        or whose file cannot be started afresh, stays in the file and is sealed with the next day,
        said at each boundary that fails; a file replaced but not reopened stops the stream the
        way a failed write does. Neither ends the session.

        Args:
            day_path_for: Where the day record of a trading day belongs
            sealed_day: The day the boundary ended — the label of a file whose header names none
            next_day: The day the boundary opened, named in the fresh file's header

        Returns:
            What the sealed day holds, or None when nothing was sealed
        """
        with self._lock:
            if not self._writing():
                return None
            day = self._trading_day or sealed_day
            day_path = day_path_for(day)
            try:
                self._writer.close()
            except OSError as e:
                self._report_failure(e)
                return None
            try:
                summary = seal_stream_day(self._path, day_path, self._time_fields, day)
            except Exception as e:   # noqa: BLE001 — sealing a record must never end a session
                self._keep_day_in_file(day, e)
                return None
            self._trading_day = next_day
            try:
                write_text_atomic(self._path, jsonl_line(self._header()))
            except OSError as e:
                # The file was not replaced and still holds the whole day, so the day record just
                # written goes again and the day is sealed with the next one, like a day that
                # could not be converted
                self._withdraw_day_record(day_path)
                self._keep_day_in_file(day, e)
                return None
            try:
                self._writer = JsonlStreamWriter(self._path, append=True)
            except OSError as e:
                self._report_failure(e)
            return summary

    def close(self) -> None:
        """Close the stream. A failure here is said only when no earlier one was."""
        with self._lock:
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
        with self._lock:
            return self._writing()

    def get_path(self) -> Path:
        """
        The stream file.

        Returns:
            Its path
        """
        return self._path

    def get_stream(self) -> str:
        """
        The stream's name, as its header gives it.

        Returns:
            The name
        """
        return self._stream

    def get_trading_day(self) -> Optional[date]:
        """
        The trading day the file holds, as its header names it.

        Returns:
            The day, or None when the header names none
        """
        return self._trading_day

    def _writing(self) -> bool:
        """
        Whether records still reach the file — the lock already held.

        Returns:
            False once the stream failed or was closed
        """
        return self._writer is not None and not self._failed and self._writer.is_open()

    def _header(self) -> Dict[str, Any]:
        """
        The file's header line: the stream, its version and run, and the day the file holds.

        Returns:
            The header record
        """
        if self._trading_day is None:
            return dict(self._header_base)
        return {**self._header_base, STREAM_TRADING_DAY_FIELD: self._trading_day.isoformat()}

    def _keep_day_in_file(self, day: date, error: Exception) -> None:
        """
        A day that could not be sealed: say so once, and go on appending to the same file, so the
        next seal takes this day with the next one.

        Args:
            day: The trading day that stays in the file
            error: Why it could not be sealed
        """
        self._trading_day = day
        if self._logger is not None:
            self._logger.error(
                f'❌ Trading day {day} of the {self._stream} stream could not be sealed '
                f'({type(error).__name__}: {error}). Its lines stay in {self._path} and are sealed '
                f'with the next day; the session continues.')
        try:
            self._writer = JsonlStreamWriter(self._path, append=True)
        except OSError as e:
            self._report_failure(e)

    def _withdraw_day_record(self, day_path: Path) -> None:
        """
        Remove a day record whose day stays in the file. Where even that fails, nothing is lost: a
        reader takes the day from its record and skips the file until the next seal writes the
        record again with the lines added since.

        Args:
            day_path: The day record to remove
        """
        try:
            day_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _report_failure(self, error: OSError, at: Optional[str] = None) -> None:
        """
        Say once that the stream cannot be written, and stop writing it.

        Args:
            error: What the file system answered
            at: Which record could not be written, or None
        """
        self._failed = True
        if self._logger is not None:
            where = f' at {at}' if at else ''
            self._logger.error(
                f'❌ The {self._stream} stream {self._path} could not be written{where}: {error}. '
                f'{self._stops}; the session continues.')
