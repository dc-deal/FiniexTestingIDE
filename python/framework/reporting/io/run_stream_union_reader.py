"""
FiniexTestingIDE - Run Stream Union Reader (#476)

A session's stream read as one: the trading days sealed out of it, oldest first, then its own file.
A backtest's stream, and a session's before its first seal, is its file alone.

These rules keep the union exact while the session seals a day underneath a reader:
- **The file is read first, the sealed days are listed after it.** A seal that happens in between
  has then either sealed the day the reader already holds — and the next rule drops it — or it has
  not, and the day is still in the file. Listed the other way round, a seal in between would leave
  a day in neither.
- **A file whose day is already sealed is left out.** Its header names the day it holds; a file
  that names none was the session's first and is sealed once any day is. This also covers a
  session that died between writing a day record and starting its file afresh: the day lies in
  both, and is read once.
- **A day record gone by the time the read asks for it was taken back.** A seal whose fresh file
  could not be started removes its day record again, and the day's lines are still in the file the
  read began with — so the day is read from there, as if it had never been sealed.

A sealed day is immutable, so what its footer says — and its first and last line — is kept in this
process by path, size and modification time. A read asks each day record for its status once — two
calls per sealed day with the listing that finds it — and answers every question it has of the day
from that. Once the days were read, a stream's two ends cost a listing of its days and the two ends
of its file; its overview costs the same plus one read of the running file, whose records it counts
by their line ends — at most one trading day of them.

A page (#580) reads a window up to a limit and stops at the day that fills it, because a stream's
records come in the order of their time: its writer stamps them from a clock that only moves
forward. Each record is numbered by its position in the whole stream — the sealed days' footer
counts before it, then its place — so a number never changes when its day is sealed. A record
without its time cannot be placed on a page, and makes the stream unreadable by pages.
"""

import bisect
import functools
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.run_stream_day_record import (
    hot_file_trading_day,
    read_day_record_lines,
    read_day_record_rows,
    read_day_record_summary,
    read_day_record_times,
    record_time_of,
)
from python.framework.reporting.io.run_stream_reader import (
    read_stream_ends,
    read_stream_lines,
    stream_ends_of_bytes,
)
from python.framework.types.run_stream_types import (
    DayRecordSummary,
    RunStreamEnds,
    RunStreamLines,
    RunStreamOverview,
    RunStreamPage,
    SealedStreamDay,
    StreamDayIndexRow,
    StreamDaySource,
    StreamTimeWindow,
)
from python.framework.utils.time_utils import floor_to_millisecond

# How many sealed days one process keeps the footer and the two end lines of — a thirty-day
# session's streams with room to spare, a few kilobytes each
_DAY_CACHE_SIZE = 4096


def read_stream_union(
    hot_path: Optional[Path],
    list_days: Callable[[], List[Path]],
    time_fields: Tuple[str, ...] = (),
    window: Optional[StreamTimeWindow] = None,
) -> Optional[RunStreamLines]:
    """
    Read a stream across its sealed days and its file — within a time window when one is given.
    A record that carries no time belongs to its day: it is read when its day is.

    Args:
        hot_path: The stream's file, whether or not it is there
        list_days: Lists the stream's sealed days, oldest first — called after the file was read
        time_fields: The fields a record takes its time from, the first one it carries
        window: The stretch to read, or None for the whole stream

    Returns:
        The stream's records, or None when the run holds no such stream
    """
    hot = read_stream_lines(hot_path) if hot_path is not None and hot_path.is_file() else None
    days = _sealed_days(list_days())
    if hot is None and not days:
        return None
    records: List[Dict[str, Any]] = []
    truncated = False
    for day in days:
        summary = day.summary
        truncated = truncated or summary.cut_last_line
        if window is not None and not window.overlaps(summary.first_time, summary.last_time):
            continue
        records.extend(read_day_record_lines(day.path, window))
    header = _newest_day_header(days)
    last_unterminated = False
    if hot is not None and not _is_sealed(hot.header, days):
        header = hot.header if hot.header is not None else header
        truncated = truncated or hot.truncated_tail
        hot_records = [line.record for line in hot.lines]
        kept = _within(hot_records, time_fields, window) if window is not None else hot_records
        records.extend(kept)
        last_unterminated = (hot.last_unterminated and bool(kept)
                             and kept[-1] is hot.lines[-1].record)
    return RunStreamLines(header=header, records=records, truncated_tail=truncated,
                          last_unterminated=last_unterminated)


def read_stream_page(
    hot_path: Optional[Path],
    list_days: Callable[[], List[Path]],
    time_fields: Tuple[str, ...],
    window: StreamTimeWindow,
    limit: int,
) -> Optional[RunStreamPage]:
    """
    One page of a stream within a time window: its records oldest first, at most `limit` of them
    unless one millisecond holds more, each numbered by its position in the whole stream (#580).

    The read stops at the day that fills the page; the days after it are only counted, a whole day
    inside the window from its footer. The page never ends inside a millisecond: the part of a
    millisecond the limit would cut moves to the next page, which starts there — a client that
    keeps time in milliseconds asks for it again without repeating or skipping a record. A
    millisecond holding more records than the limit is served whole, so the page holds more than
    the limit. A record that carries no time cannot be placed on a page and makes the stream
    unreadable: a stream read by pages is ordered by the one time each of its records carries.

    Args:
        hot_path: The stream's file, whether or not it is there
        list_days: Lists the stream's sealed days, oldest first — called after the file was read
        time_fields: The fields a record takes its time from, the first one it carries
        window: The stretch to read
        limit: The most records the page holds, at least one — exceeded only by a millisecond
            that holds more

    Returns:
        The page, or None when the run holds no such stream
    """
    hot = read_stream_lines(hot_path) if hot_path is not None and hot_path.is_file() else None
    days = _sealed_days(list_days())
    if hot is None and not days:
        return None
    # (record number, time, record, whether it is a line whose newline was never written)
    collected: List[Tuple[int, Optional[datetime], Dict[str, Any], bool]] = []
    matched = 0
    offset = 0
    closed = False
    truncated_tail = False
    newest: Optional[datetime] = None
    for day in days:
        summary = day.summary
        truncated_tail = truncated_tail or summary.cut_last_line
        newest = summary.last_time if summary.last_time is not None else newest
        if window.overlaps(summary.first_time, summary.last_time):
            if closed:
                matched += _day_count_within(day.path, summary, window)
            else:
                rows = read_day_record_rows(day.path, window)
                if any(moment is None for _, moment, _ in rows):
                    raise _record_without_time(day.path)
                matched += len(rows)
                collected.extend((offset + position + 1, moment, record, False)
                                 for position, moment, record in rows)
                closed = _page_is_closed(collected, limit)
        offset += summary.record_count
    header = _newest_day_header(days)
    if hot is not None and not _is_sealed(hot.header, days):
        header = hot.header if hot.header is not None else header
        truncated_tail = truncated_tail or hot.truncated_tail
        times = [record_time_of(line.record, time_fields) for line in hot.lines]
        if any(moment is None for moment in times):
            raise _record_without_time(hot_path)
        newest = times[-1] if times else newest
        last_position = len(hot.lines) - 1
        for position in _kept_positions(times, window):
            matched += 1
            if not closed:
                unterminated = hot.last_unterminated and position == last_position
                collected.append((offset + position + 1, times[position],
                                  hot.lines[position].record, unterminated))
    served, next_time = _page_cut(collected, limit)
    page = collected[:served]
    truncated = served < matched
    return RunStreamPage(
        header=header, records=[record for _, _, record, _ in page],
        record_nos=[record_no for record_no, _, _, _ in page], matched=matched,
        truncated=truncated, next_time=next_time if truncated else None, newest_time=newest,
        truncated_tail=truncated_tail, last_unterminated=bool(page) and page[-1][3])


def read_stream_union_ends(
    hot_path: Optional[Path],
    list_days: Callable[[], List[Path]],
) -> Optional[RunStreamEnds]:
    """
    A stream's header, its first record and its latest one across its sealed days and its file,
    without reading the records between. Right after a seal the file holds no record yet, and the
    latest one is the newest day's last.

    Args:
        hot_path: The stream's file, whether or not it is there
        list_days: Lists the stream's sealed days, oldest first — called after the file was read

    Returns:
        The two ends, or None when the run holds no such stream
    """
    hot = read_stream_ends(hot_path) if hot_path is not None and hot_path.is_file() else None
    days = _sealed_days(list_days())
    if hot is None and not days:
        return None
    return _union_ends(hot, days)


def read_stream_union_overview(
    hot_path: Optional[Path],
    list_days: Callable[[], List[Path]],
    time_fields: Tuple[str, ...] = (),
) -> Optional[RunStreamOverview]:
    """
    A stream's two ends and its trading days from one read of its file (#580): the days a sealed
    day's footer describes, the running file counted by its line ends, and the stream's header,
    first and latest record across both — no record between them is read.

    Args:
        hot_path: The stream's file, whether or not it is there
        list_days: Lists the stream's sealed days, oldest first — called after the file was read
        time_fields: The fields a record takes its time from, the first one it carries

    Returns:
        The overview, or None when the run holds no such stream
    """
    hot_ends: Optional[RunStreamEnds] = None
    hot_count = 0
    hot_size = 0
    if hot_path is not None and hot_path.is_file():
        # One read: a seal between two reads would describe two different files
        data = hot_path.read_bytes()
        hot_size = len(data)
        hot_ends = stream_ends_of_bytes(data, hot_path)
        hot_count = _record_count(data, hot_ends)
    days = _sealed_days(list_days())
    if hot_ends is None and not days:
        return None
    rows: List[StreamDayIndexRow] = []
    for day in days:
        summary = day.summary
        rows.append(StreamDayIndexRow(
            trading_day=summary.trading_day, source=StreamDaySource.SEALED,
            record_count=summary.record_count, first_time=summary.first_time,
            last_time=summary.last_time, size_bytes=day.size_bytes))
    if hot_ends is not None and not _is_sealed(hot_ends.header, days):
        rows.append(StreamDayIndexRow(
            trading_day=hot_file_trading_day(hot_ends.header), source=StreamDaySource.HOT,
            record_count=hot_count,
            first_time=record_time_of(hot_ends.first, time_fields) if hot_ends.first else None,
            last_time=record_time_of(hot_ends.last, time_fields) if hot_ends.last else None,
            size_bytes=hot_size))
    return RunStreamOverview(ends=_union_ends(hot_ends, days), days=rows)


def _union_ends(hot: Optional[RunStreamEnds], days: List[SealedStreamDay]) -> RunStreamEnds:
    """
    A stream's header, first and latest record across its sealed days and its file's two ends —
    the file left out when its day is sealed.

    Args:
        hot: The file's two ends, None when it is not there
        days: The stream's sealed days, oldest first

    Returns:
        The stream's two ends
    """
    filled = [day for day in days if day.summary.record_count]
    first = _day_end_record(filled[0], first=True) if filled else None
    last = _day_end_record(filled[-1], first=False) if filled else None
    header = _newest_day_header(days)
    truncated = any(day.summary.cut_last_line for day in days)
    if hot is not None and not _is_sealed(hot.header, days):
        header = hot.header if hot.header is not None else header
        first = first if first is not None else hot.first
        last = hot.last if hot.last is not None else last
        truncated = truncated or hot.truncated_tail
    return RunStreamEnds(header=header, first=first, last=last, truncated_tail=truncated)


def _sealed_days(day_paths: List[Path]) -> List[SealedStreamDay]:
    """
    The listed days with what their footers say — each day record asked for its status once, its
    footer read once per process, since the day never changes. A day record gone since it was
    listed was taken back by a seal whose fresh file could not be started: its lines are still in
    the stream's file, which was read before the days were listed, so the day is left out here.

    Args:
        day_paths: The stream's sealed days as listed, oldest first

    Returns:
        The days still there, oldest first
    """
    days: List[SealedStreamDay] = []
    for day_path in day_paths:
        try:
            stat = day_path.stat()
        except FileNotFoundError:
            continue
        days.append(SealedStreamDay(
            path=day_path, summary=_cached_summary(str(day_path), stat.st_size, stat.st_mtime_ns),
            size_bytes=stat.st_size, modified_ns=stat.st_mtime_ns))
    return days


@functools.lru_cache(maxsize=_DAY_CACHE_SIZE)
def _cached_summary(path: str, size: int, mtime_ns: int) -> DayRecordSummary:
    """
    A day record's summary, kept by its path, size and modification time.

    Args:
        path: The day record
        size: Its size, part of the key
        mtime_ns: Its modification time, part of the key

    Returns:
        Its summary
    """
    return read_day_record_summary(Path(path))


def _newest_day_header(days: List[SealedStreamDay]) -> Optional[Dict[str, Any]]:
    """
    The header line the newest sealed day's file opened with — a copy, since the summary it comes
    from is kept for the whole process.

    Args:
        days: The stream's sealed days, oldest first

    Returns:
        The header, or None when there is no day or it had none
    """
    if not days:
        return None
    header = days[-1].summary.header
    return dict(header) if header is not None else None


def _day_end_record(day: SealedStreamDay, first: bool) -> Optional[Dict[str, Any]]:
    """
    The first or the last record of a sealed day.

    Args:
        day: The sealed day
        first: True for the first record, False for the last

    Returns:
        The record, or None when the day holds none
    """
    first_text, last_text = _cached_end_lines(str(day.path), day.size_bytes, day.modified_ns)
    text = first_text if first else last_text
    if text is None:
        return None
    try:
        record = json.loads(text)
    except json.JSONDecodeError as e:
        raise ReportArtifactUnreadableError(day.path.name, str(day.path), str(e)) from e
    if not isinstance(record, dict):
        raise ReportArtifactUnreadableError(day.path.name, str(day.path), 'not a JSON object')
    return record


@functools.lru_cache(maxsize=_DAY_CACHE_SIZE)
def _cached_end_lines(path: str, size: int, mtime_ns: int) -> Tuple[Optional[str], Optional[str]]:
    """
    A day record's first and last line as written, kept by its path, size and modification time.

    Args:
        path: The day record
        size: Its size, part of the key
        mtime_ns: Its modification time, part of the key

    Returns:
        (the first line, the last line), each None when the day holds none
    """
    try:
        lines = pq.read_table(path, columns=['line']).column('line')
    except (OSError, pa.ArrowInvalid) as e:
        raise ReportArtifactUnreadableError(Path(path).name, path, str(e)) from e
    if len(lines) == 0:
        return None, None
    return lines[0].as_py(), lines[len(lines) - 1].as_py()


def _is_sealed(header: Optional[Dict[str, Any]], days: List[SealedStreamDay]) -> bool:
    """
    Whether the day a stream's file holds is already among its sealed days.

    Args:
        header: The file's header line
        days: The stream's sealed days

    Returns:
        True when the file is to be left out of the union
    """
    if not days:
        return False
    day: Optional[date] = hot_file_trading_day(header)
    if day is None:
        # The session's first file names no day; once any day is sealed, it was sealed with it
        return True
    return any(sealed.summary.trading_day == day for sealed in days)


def _within(records: List[Dict[str, Any]], time_fields: Tuple[str, ...],
            window: StreamTimeWindow) -> List[Dict[str, Any]]:
    """
    The records of a stream's file inside a time window. A record without a time is kept when the
    file's records with one reach into the window, or when none carries one.

    Args:
        records: The file's records, in order
        time_fields: The fields a record takes its time from
        window: The stretch to read

    Returns:
        The records kept, in order
    """
    times = [record_time_of(record, time_fields) for record in records]
    return [records[position] for position in _kept_positions(times, window)]


def _kept_positions(times: List[Optional[datetime]], window: StreamTimeWindow) -> List[int]:
    """
    Which records of a stream's file lie inside a time window — the rule `_within` states, by
    position.

    Args:
        times: Each record's time, None for a record that carries none, in order
        window: The stretch to read

    Returns:
        The positions kept, in order
    """
    present = [moment for moment in times if moment is not None]
    span_inside = window.overlaps(min(present), max(present)) if present else True
    return [position for position, moment in enumerate(times)
            if (window.holds(moment) if moment is not None else span_inside)]


def _day_count_within(day_path: Path, summary: DayRecordSummary,
                      window: StreamTimeWindow) -> int:
    """
    How many records of a sealed day lie inside a time window — from its footer when the whole day
    does, else from its time column, without its lines. A record without its time is unreadable
    here as on a page.

    Args:
        day_path: The day record
        summary: Its footer
        window: The stretch counted

    Returns:
        The count
    """
    if (summary.first_time is not None and summary.last_time is not None
            and window.holds(summary.first_time) and window.holds(summary.last_time)):
        return summary.record_count
    times = read_day_record_times(day_path)
    if any(moment is None for moment in times):
        raise _record_without_time(day_path)
    return sum(1 for moment in times if window.holds(moment))


def _record_without_time(path: Path) -> ReportArtifactUnreadableError:
    """
    The error for a stream read by pages whose record carries no time to place it by.

    Args:
        path: The file the record lies in — a day record or the stream's own file

    Returns:
        The error, to raise
    """
    return ReportArtifactUnreadableError(
        path.name, str(path), 'a record carries no time, so it cannot be placed on a page')


def _page_cut(rows: List[Tuple[int, Optional[datetime], Dict[str, Any], bool]],
              limit: int) -> Tuple[int, Optional[datetime]]:
    """
    Where a page of a stream's records ends: after `limit` records, moved back to the start of the
    millisecond the limit falls in — or, when that millisecond holds the whole page, after it.

    Args:
        rows: The records read, oldest first
        limit: The most records the page holds

    Returns:
        (how many records the page holds, the start of the millisecond the rest begins in — None
        when the page holds every record read)
    """
    if len(rows) <= limit:
        return len(rows), None
    buckets = _millisecond_buckets(rows)
    boundary = buckets[limit]
    first = bisect.bisect_left(buckets, boundary)
    if first > 0:
        return first, boundary
    after = bisect.bisect_right(buckets, boundary)
    if after < len(buckets):
        return after, buckets[after]
    return len(rows), None


def _page_is_closed(rows: List[Tuple[int, Optional[datetime], Dict[str, Any], bool]],
                    limit: int) -> bool:
    """
    Whether the records read already decide where the page ends, so no further day is read.

    Args:
        rows: The records read, oldest first
        limit: The most records the page holds

    Returns:
        True when the page leaves some of them out
    """
    return len(rows) > limit and _page_cut(rows, limit)[0] < len(rows)


def _millisecond_buckets(rows: List[Tuple[int, Optional[datetime], Dict[str, Any], bool]]
                         ) -> List[datetime]:
    """
    The millisecond each record of a page falls in — every record of a page carries its time.

    Args:
        rows: The records read, oldest first

    Returns:
        One instant per record, its microseconds cut to the millisecond, never decreasing
    """
    return [floor_to_millisecond(moment) for _, moment, _, _ in rows]


def _record_count(data: bytes, ends: RunStreamEnds) -> int:
    """
    How many records a stream's file holds, counted by its lines rather than parsed.

    Args:
        data: The file's bytes
        ends: Its two ends, which say whether it has a header and a last line cut off

    Returns:
        The count of records
    """
    pieces = data.split(b'\n')
    trailing = pieces.pop()
    complete = sum(1 for piece in pieces if piece.strip())
    count = complete - (1 if ends.header is not None else 0)
    if trailing.strip() and not ends.truncated_tail:
        count += 1
    return count
