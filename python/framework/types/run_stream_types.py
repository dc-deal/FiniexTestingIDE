"""
FiniexTestingIDE - Run Stream Types

What is read from a run stream without reading all of it (#611): its two ends. And what a session's
stream is made of once its trading days are sealed (#476): where its lines lie, what a sealed day
says about itself, and a stream read whole or within a time window.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


@dataclass(frozen=True)
class RunStreamEnds:
    """
    A run stream's header, its first record and its latest one, read without the lines between.

    Args:
        header: The stream's first line when it is a header, else None
        first: The first record after the header, or None when the stream holds none yet
        last: The last complete record, or None when the stream holds none yet
        truncated_tail: Whether a last line cut off mid-write was left out
    """
    header: Optional[Dict[str, Any]]
    first: Optional[Dict[str, Any]]
    last: Optional[Dict[str, Any]]
    truncated_tail: bool


@dataclass(frozen=True)
class RunStreamFileLine:
    """
    One record of a stream's file, with the text it was written as.

    Args:
        line_no: The line's number in the file, from 1
        text: The line as written, without its newline
        record: The line parsed
    """
    line_no: int
    text: str
    record: Dict[str, Any]


@dataclass(frozen=True)
class RunStreamFile:
    """
    A stream's file read whole: its header and every record after it.

    Args:
        header: The file's header line, or None when it has none
        lines: Every record, in file order
        truncated_tail: Whether a last line cut off mid-write was left out
        last_unterminated: Whether the last record is a line whose newline was never written
    """
    header: Optional[Dict[str, Any]]
    lines: List[RunStreamFileLine]
    truncated_tail: bool
    last_unterminated: bool


@dataclass(frozen=True)
class RunStreamSources:
    """
    Where one run stream's lines lie: its own file and the trading days sealed out of it.

    Args:
        hot_path: The stream's own file, or None when it is not there
        day_paths: Its sealed days, oldest first
    """
    hot_path: Optional[Path]
    day_paths: Tuple[Path, ...]

    def exists(self) -> bool:
        """
        Whether the run holds the stream at all.

        Returns:
            True when its file or a sealed day is there
        """
        return self.hot_path is not None or bool(self.day_paths)


@dataclass(frozen=True)
class StreamTimeWindow:
    """
    A stretch of time a stream is read within — records whose own time lies in [since, until).

    Args:
        since: The first instant read, or None for the stream's start
        until: The first instant no longer read, or None for its end
    """
    since: Optional[datetime] = None
    until: Optional[datetime] = None

    def holds(self, instant: datetime) -> bool:
        """
        Whether an instant lies inside the window.

        Args:
            instant: The instant

        Returns:
            True when it does
        """
        return ((self.since is None or instant >= self.since)
                and (self.until is None or instant < self.until))

    def overlaps(self, first: Optional[datetime], last: Optional[datetime]) -> bool:
        """
        Whether a span of records can hold one inside the window.

        Args:
            first: The span's earliest record time, None when no record carries one
            last: Its latest, None when no record carries one

        Returns:
            True when it can — always for a span whose records carry no time
        """
        if first is None or last is None:
            return True
        return ((self.until is None or first < self.until)
                and (self.since is None or last >= self.since))


@dataclass(frozen=True)
class DayRecordSummary:
    """
    What a sealed trading day of a stream says about itself, read from its footer without its
    lines (#476).

    Args:
        stream: The stream's name
        schema_version: The version of its line shapes
        run_id: The run it belongs to
        trading_day: The trading day it holds, labelled by the date the day opened
        record_count: How many records it holds
        first_time: The earliest record time among its lines, None when no line carries one
        last_time: The latest one, None when no line carries one
        header: The header line the day's file opened with, None when it had none
        cut_last_line: Whether a last line cut off mid-write was left out when the day was sealed
    """
    stream: Optional[str]
    schema_version: Optional[Union[int, str]]
    run_id: Optional[str]
    trading_day: date
    record_count: int
    first_time: Optional[datetime]
    last_time: Optional[datetime]
    header: Optional[Dict[str, Any]]
    cut_last_line: bool


@dataclass(frozen=True)
class RunStreamLines:
    """
    A run stream read across its sealed days and its own file, in the order it was written.

    Args:
        header: The stream's header — its own file's, or the newest sealed day's when that file
            is left out or gone
        records: Every record, header lines left out, oldest first
        truncated_tail: Whether a last line cut off mid-write was left out — at the end of the
            stream's file, or of a day that was sealed with one
        last_unterminated: Whether the last record is a line of the stream's file whose newline
            was never written — complete JSON that a model may still refuse as cut off
    """
    header: Optional[Dict[str, Any]]
    records: List[Dict[str, Any]]
    truncated_tail: bool
    last_unterminated: bool


class StreamDaySource(StrEnum):
    """Where one trading day of a stream lies."""
    SEALED = 'sealed'    # a day record: written once, whole, when the day ended
    HOT = 'hot'          # the stream's own file: the running day, or the last one of a session


@dataclass(frozen=True)
class StreamDayIndexRow:
    """
    One trading day of a run stream, as its index lists it — read without its records.

    Args:
        trading_day: The day, or None for a file that names none — a backtest's stream, or a
            session's file before its first day was sealed
        source: Where the day lies
        record_count: How many records it holds
        first_time: Its earliest record time, None when no record carries one
        last_time: Its latest one, None when no record carries one
        size_bytes: The size of the file it lies in
    """
    trading_day: Optional[date]
    source: StreamDaySource
    record_count: int
    first_time: Optional[datetime]
    last_time: Optional[datetime]
    size_bytes: int
