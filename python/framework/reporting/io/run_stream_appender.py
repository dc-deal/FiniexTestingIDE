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

A stream of the money record — the order events and the trades — keeps what it could not write
instead (#632): its lines wait in memory, in their order, and are written again when the session
asks (`sync`), after the file is cut back to where its last line forced to the disk ends. A failed
force counts the lines written since the last good one as lost too, and writes them again with the
rest: a retried force can answer success over pages the system already dropped. Such a stream says
its first failure on the session's channel and to whoever holds the session's entries over it, and
forces its lines to the disk at the session's cadence while it writes.

The state frames (#579), the tick stream record (#609), the trades, the diagnostics rows (#476) and
the order events (#362) are written through it. The acceptance studies' captures keep their own
copy of these rules.
"""

import threading
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from python.framework.constants.run_layout_constants import (
    STREAM_HEADER_KIND,
    STREAM_TRADING_DAY_FIELD,
)
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter, jsonl_line
from python.framework.reporting.io.run_stream_day_record import seal_stream_day
from python.framework.types.run_stream_types import DayRecordSummary
from python.framework.types.store_types import StoreId
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
        keeps_unwritten: A failed write keeps its line and every later one in memory, to be written
            again by `sync` — for the money record (#632)
        on_failure: Told the first failure of each stretch in which the stream cannot be written;
            None for nobody
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
        keeps_unwritten: bool = False,
        on_failure: Optional[Callable[[OSError], None]] = None,
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
        self._keeps_unwritten = keeps_unwritten
        self._on_failure = on_failure
        # The money record's bookkeeping (#632): the lines not on the disk yet, in their order; the
        # lines written since the last force to the disk, which a failed force loses; and the
        # length of the file where that force ended
        self._unwritten: List[str] = []
        self._unforced: List[str] = []
        self._forced_size = 0
        try:
            self._writer = JsonlStreamWriter(self._path)
            self._writer.write(self._header())
            if self._keeps_unwritten:
                self._unforced = [jsonl_line(self._header())]
        except OSError as e:
            if required:
                if self._writer is not None:
                    self._writer.close()
                raise
            if self._keeps_unwritten:
                # Nothing of the file is known to stand: it is started afresh when written again
                if self._writer is not None:
                    self._writer.close()
                    self._writer = None
                self._fail_kept(e, [jsonl_line(self._header())])
            else:
                self._report_failure(e)

    def write(self, record: Dict[str, Any], at: Optional[str] = None) -> bool:
        """
        Append one record as one line; the first failure is said and ends the writing — or, in a
        stream that keeps what it could not write, keeps this line and every later one for `sync`.

        Args:
            record: JSON-serializable values; None values are left out of the line
            at: Which record it is, named in the failure message — such as its sequence number

        Returns:
            True when the line was written
        """
        with self._lock:
            if self._keeps_unwritten:
                return self._write_kept(jsonl_line(record), at)
            if not self._writing():
                return False
            try:
                self._writer.write(record)
                return True
            except OSError as e:
                self._report_failure(e, at)
                return False

    def sync(self) -> bool:
        """
        Bring a stream that keeps what it could not write to the disk (#632): while it is failing,
        cut the file back to its last forced line and write everything since again; otherwise force
        the lines written since the last time. A stream that keeps nothing does nothing here.

        Returns:
            True when every line the stream was given is on the disk, forced
        """
        with self._lock:
            if not self._keeps_unwritten:
                return True
            if self._failed:
                return self._write_again()
            if not self._unforced or self._writer is None or not self._writer.is_open():
                return True
            try:
                self._writer.sync()
            except OSError as e:
                self._fail_kept(e, [])
                return False
            self._forced_size = self._writer.get_size()
            self._unforced = []
            return True

    def is_failing(self) -> bool:
        """
        Whether a stream that keeps what it could not write is holding lines it could not write.

        Returns:
            True from a failed write or force until `sync` has written them again
        """
        with self._lock:
            return self._keeps_unwritten and self._failed

    def get_unwritten_count(self) -> int:
        """
        How many lines a stream that keeps what it could not write is holding in memory.

        Returns:
            The number of lines still to be written
        """
        with self._lock:
            return len(self._unwritten)

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
            header = jsonl_line(self._header())
            try:
                write_text_atomic(self._path, header, StoreId.RUNS)
            except OSError as e:
                # The file was not replaced and still holds the whole day, so the day record just
                # written goes again and the day is sealed with the next one, like a day that
                # could not be converted
                self._withdraw_day_record(day_path)
                self._keep_day_in_file(day, e)
                return None
            # The day's lines are in its day record and the fresh file holds its header, both
            # written durably: nothing of the money record waits to be forced
            self._forced_size = len(header.encode('utf-8'))
            self._unforced = []
            try:
                self._writer = JsonlStreamWriter(self._path, append=True)
            except OSError as e:
                self._report_failure(e)
            return summary

    def close(self) -> None:
        """
        Close the stream. A failure here is said only when no earlier one was. A stream that keeps
        what it could not write makes one last attempt first, and says what is lost when it fails.
        """
        with self._lock:
            if self._keeps_unwritten:
                self._last_attempt()
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

    def _write_kept(self, line: str, at: Optional[str]) -> bool:
        """
        Write one line of a stream that keeps what it could not write — the lock already held.

        Args:
            line: The line
            at: Which record it is, named in the failure message

        Returns:
            True when the line was written; False when it waits in memory
        """
        if self._failed:
            self._unwritten.append(line)
            return False
        if self._writer is None or not self._writer.is_open():
            return False
        try:
            self._writer.write_line(line)
        except OSError as e:
            self._fail_kept(e, [line], at)
            return False
        self._unforced.append(line)
        return True

    def _fail_kept(self, error: OSError, lines: List[str], at: Optional[str] = None) -> None:
        """
        A write or a force failed: every line since the last force waits in memory with the given
        ones, in their order — the system may have dropped them though it took them. The first
        failure of a stretch is said, and told to whoever holds the entries over it.

        Args:
            error: What the file system answered
            lines: The lines that did not reach the file, after those already taken
            at: Which record could not be written, or None
        """
        self._unwritten = self._unforced + lines + self._unwritten
        self._unforced = []
        if self._failed:
            return
        self._failed = True
        if self._logger is not None:
            where = f' at {at}' if at else ''
            self._logger.error(
                f'❌ The {self._stream} stream {self._path} could not be written{where}: {error}. '
                f'{self._stops}; its lines are kept in memory and written again, and the session '
                f'continues.')
        if self._on_failure is not None:
            self._on_failure(error)

    def _write_again(self) -> bool:
        """
        Write the lines kept in memory: cut the file back to its last forced line — a line half on
        the disk lies past it — then write them in their order and force them — the lock already
        held. A file that could not be opened is started afresh with them.

        Returns:
            True when every kept line is on the disk; False when they still wait
        """
        try:
            if self._writer is None or not self._writer.is_open():
                self._writer = JsonlStreamWriter(self._path, append=self._forced_size > 0)
            self._writer.cut_to(self._forced_size)
            for line in self._unwritten:
                self._writer.write_line(line)
            self._writer.sync()
        except OSError:
            return False
        self._forced_size = self._writer.get_size()
        self._unwritten = []
        self._failed = False
        return True

    def _last_attempt(self) -> None:
        """
        At the close of a stream that keeps what it could not write: write what waits, or force what
        was written; say what is lost when neither works — the lock already held.
        """
        if self._failed:
            if self._write_again():
                return
            if self._logger is not None:
                self._logger.error(
                    f'❌ {len(self._unwritten)} line(s) of the {self._stream} stream could not be '
                    f'written by the end of the session and are lost; {self._path} ends before them.')
            self._unwritten = []
            return
        if self._unforced and self._writer is not None and self._writer.is_open():
            try:
                self._writer.sync()
            except OSError as e:
                if self._logger is not None:
                    self._logger.error(
                        f'❌ The {self._stream} stream {self._path} could not be forced to the disk '
                        f'at its close: {e}. Its last {len(self._unforced)} line(s) may be lost if '
                        f'the machine stops before the system writes them.')
            self._unforced = []

    def _report_failure(self, error: OSError, at: Optional[str] = None) -> None:
        """
        Say once that the stream cannot be written, and stop writing it — or, in a stream that keeps
        what it could not write, keep what it has not forced for `sync`.

        Args:
            error: What the file system answered
            at: Which record could not be written, or None
        """
        if self._keeps_unwritten:
            self._fail_kept(error, [], at)
            return
        self._failed = True
        if self._logger is not None:
            where = f' at {at}' if at else ''
            self._logger.error(
                f'❌ The {self._stream} stream {self._path} could not be written{where}: {error}. '
                f'{self._stops}; the session continues.')
