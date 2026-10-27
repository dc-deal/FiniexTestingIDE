"""
FiniexTestingIDE - Run Stream Day Record (#476)

One trading day of a session's stream, sealed: the lines the stream's file held when the day
ended, moved into an immutable parquet file that is written once, whole, and never again.

The day record keeps every line exactly as it was written — a reader gets back the bytes the
stream's file held, so no report, route or read-back can change because a day was sealed. Beside
each line it keeps what a reader selects by without parsing the line: its position, its time — the
first of the stream's declared time fields the line carries — and its kind. Its footer says what
the day holds, so a stream's index is answered from the footers alone (DDIA ch. 3: a log is cut
into segments, and a closed segment is never written again).

A last line without its newline that does not parse is a line cut off mid-write: it is left out,
and the footer says so — the rule the stream readers follow. A complete line that does not parse,
a line that is not a JSON object and a byte that is not UTF-8 are a damaged file, and nothing is
sealed from it.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from python.framework.constants.run_layout_constants import (
    DAY_RECORD_FORMAT_VERSION,
    STREAM_TRADING_DAY_FIELD,
)
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.run_stream_reader import read_stream_lines
from python.framework.types.run_stream_types import DayRecordSummary, StreamTimeWindow
from python.framework.utils.atomic_file_utils import write_file_atomic
from python.framework.utils.time_utils import parse_iso_datetime

# The footer key a day record's own description is kept under
_FOOTER_KEY = b'finiex.day_record'

_SCHEMA = pa.schema([
    ('line_no', pa.int32()),
    ('record_time', pa.timestamp('us', tz='UTC')),
    ('record_kind', pa.string()),
    ('line', pa.string()),
])


def seal_stream_day(
    hot_path: Path,
    day_path: Path,
    time_fields: Tuple[str, ...],
    trading_day: date,
) -> DayRecordSummary:
    """
    Seal one trading day of a stream: its file's lines into the day record, written atomically.
    The stream's file is only read.

    Args:
        hot_path: The stream's file, closed, holding the day
        day_path: Where the day record belongs
        time_fields: The fields a line takes its time from, the first one it carries
        trading_day: The day the file holds

    Returns:
        What the day record says about itself
    """
    stream_file = read_stream_lines(hot_path)
    header = stream_file.header
    line_nos: List[int] = []
    times: List[Optional[datetime]] = []
    kinds: List[Optional[str]] = []
    texts: List[str] = []
    for line in stream_file.lines:
        line_nos.append(line.line_no)
        times.append(record_time_of(line.record, time_fields))
        kinds.append(_record_kind_of(line.record))
        texts.append(line.text)
    present = [moment for moment in times if moment is not None]
    summary = DayRecordSummary(
        stream=_header_value(header, 'stream'),
        schema_version=_header_value(header, 'schema_version'),
        run_id=_header_value(header, 'run_id'),
        trading_day=trading_day,
        record_count=len(texts),
        first_time=min(present) if present else None,
        last_time=max(present) if present else None,
        header=header,
        cut_last_line=stream_file.truncated_tail,
    )
    table = pa.Table.from_arrays(
        [pa.array(line_nos, pa.int32()), pa.array(times, pa.timestamp('us', tz='UTC')),
         pa.array(kinds, pa.string()), pa.array(texts, pa.string())],
        schema=_SCHEMA.with_metadata({_FOOTER_KEY: _footer_of(summary)}))
    # Durable: the stream's file is replaced with a fresh one right after this, and from then on
    # the day record is the day's only copy
    write_file_atomic(day_path, lambda temporary: pq.write_table(
        table, temporary, compression='zstd'), durable=True)
    return summary


def read_day_record_summary(day_path: Path) -> DayRecordSummary:
    """
    What a sealed day says about itself, read from its footer alone.

    Args:
        day_path: The day record

    Returns:
        Its summary
    """
    try:
        metadata = pq.read_schema(day_path).metadata or {}
    except (OSError, pa.ArrowInvalid) as e:
        raise ReportArtifactUnreadableError(day_path.name, str(day_path), str(e)) from e
    raw = metadata.get(_FOOTER_KEY)
    if raw is None:
        raise ReportArtifactUnreadableError(
            day_path.name, str(day_path), 'its footer carries no day-record description')
    try:
        footer = json.loads(raw)
        if footer.get('format_version') != DAY_RECORD_FORMAT_VERSION:
            raise ReportArtifactUnreadableError(
                day_path.name, str(day_path),
                f"format_version {footer.get('format_version')!r}, this reader knows "
                f'{DAY_RECORD_FORMAT_VERSION}')
        return DayRecordSummary(
            stream=footer.get('stream'),
            schema_version=footer.get('schema_version'),
            run_id=footer.get('run_id'),
            trading_day=date.fromisoformat(footer['trading_day']),
            record_count=int(footer['record_count']),
            first_time=_instant(footer.get('first_time')),
            last_time=_instant(footer.get('last_time')),
            header=footer.get('header'),
            cut_last_line=bool(footer.get('cut_last_line', False)),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise ReportArtifactUnreadableError(day_path.name, str(day_path), str(e)) from e


def read_day_record_lines(
    day_path: Path,
    window: Optional[StreamTimeWindow] = None,
) -> List[Dict[str, Any]]:
    """
    The records of a sealed day, oldest first — within a time window when one is given. A record
    whose line carries no time belongs to its day and is kept.

    Args:
        day_path: The day record
        window: The stretch to read, or None for the whole day

    Returns:
        The records
    """
    try:
        table = pq.read_table(day_path, columns=['record_time', 'line'])
    except (OSError, pa.ArrowInvalid) as e:
        raise ReportArtifactUnreadableError(day_path.name, str(day_path), str(e)) from e
    times = table.column('record_time').to_pylist()
    texts = table.column('line').to_pylist()
    records: List[Dict[str, Any]] = []
    for index, (moment, text) in enumerate(zip(times, texts)):
        if window is not None and moment is not None and not window.holds(moment):
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError as e:
            raise ReportArtifactUnreadableError(
                day_path.name, str(day_path), f'row {index + 1}: {e}') from e
        if not isinstance(record, dict):
            raise ReportArtifactUnreadableError(
                day_path.name, str(day_path), f'row {index + 1}: not a JSON object')
        records.append(record)
    return records


def record_time_of(record: Dict[str, Any], time_fields: Tuple[str, ...]) -> Optional[datetime]:
    """
    A record's own time: the first of the stream's time fields the record carries.

    Args:
        record: The record
        time_fields: The stream's declared time fields, in order

    Returns:
        The instant in UTC — the first field that reads as one — or None when none does
    """
    for field in time_fields:
        value = record.get(field)
        if isinstance(value, str) and value:
            try:
                return parse_iso_datetime(value)
            except ValueError:
                continue
    return None


def hot_file_trading_day(header: Optional[Dict[str, Any]]) -> Optional[date]:
    """
    The trading day a stream's file holds, as its header names it.

    Args:
        header: The file's header line, or None

    Returns:
        The day, or None when the header names none — a backtest's stream, or a session's file
        before its first day was sealed
    """
    value = _header_value(header, STREAM_TRADING_DAY_FIELD)
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _record_kind_of(record: Dict[str, Any]) -> Optional[str]:
    """
    What kind of record a line is, where its stream says: its record kind, or its record plane.

    Args:
        record: The record

    Returns:
        The kind, or None when the line names none
    """
    for field in ('record_kind', 'record_plane'):
        value = record.get(field)
        if isinstance(value, str):
            return value
    return None


def _header_value(header: Optional[Dict[str, Any]], key: str) -> Any:
    """
    One value of a header line.

    Args:
        header: The header line, or None
        key: The value's key

    Returns:
        The value, or None
    """
    return header.get(key) if header is not None else None


def _footer_of(summary: DayRecordSummary) -> bytes:
    """
    The footer a day record describes itself with.

    Args:
        summary: What the day holds

    Returns:
        The footer's value, JSON
    """
    return json.dumps({
        'format_version': DAY_RECORD_FORMAT_VERSION,
        'stream': summary.stream,
        'schema_version': summary.schema_version,
        'run_id': summary.run_id,
        'trading_day': summary.trading_day.isoformat(),
        'record_count': summary.record_count,
        'first_time': summary.first_time.isoformat() if summary.first_time else None,
        'last_time': summary.last_time.isoformat() if summary.last_time else None,
        'header': summary.header,
        'cut_last_line': summary.cut_last_line,
    }).encode('utf-8')


def _instant(value: Optional[str]) -> Optional[datetime]:
    """
    A footer's instant.

    Args:
        value: The instant as ISO-8601, or None

    Returns:
        The instant in UTC, or None
    """
    return parse_iso_datetime(value) if value else None
