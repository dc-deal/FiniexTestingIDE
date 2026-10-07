"""
FiniexTestingIDE - Order-Event Stream IO (#362)

The run's order-event stream on disk: `io/order_events.jsonl`, one transition per line. A header
line comes first and carries the schema version once — DDIA's answer for a file of many records
written by one writer. Every later line is an OrderEventRow, the same model the API serves, so
the file and the route cannot describe an event differently.

Live, the session writes each event the moment it is recorded (OrderEventStreamWriter); a backtest
carries its events back from the scenario subprocesses and the report writes them all at once. Either way the reader
tolerates a cut-off last line — a session killed while writing it — and says so.
"""

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import ValidationError

from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.artifact_specs import ORDER_EVENTS_STREAM
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.types.api.report_types import OrderEventRow
from python.framework.types.trading_env_types.order_event_types import OrderEvent
from python.framework.utils.time_utils import parse_datetime

ORDER_EVENTS_SCHEMA_VERSION = 1
_HEADER_KIND = 'header'


def order_event_header(run_id: str) -> Dict[str, Any]:
    """
    The stream's first line.

    Args:
        run_id: The run the stream belongs to

    Returns:
        The header record
    """
    return {
        'record_kind': _HEADER_KIND,
        'stream': 'order_events',
        'schema_version': ORDER_EVENTS_SCHEMA_VERSION,
        'run_id': run_id,
    }


def order_event_row(event: OrderEvent, scenario_name: str) -> OrderEventRow:
    """
    One recorded event as the row the file holds and the API serves.

    Args:
        event: The executor's record
        scenario_name: The unit it belongs to

    Returns:
        The row
    """
    values = asdict(event)
    for stamp in ('event_time', 'ts_init'):
        moment: Optional[datetime] = values[stamp]
        values[stamp] = moment.isoformat() if moment is not None else None
    return OrderEventRow(scenario_name=scenario_name, **values)


def order_event_from_row(row: OrderEventRow) -> OrderEvent:
    """
    A row read back as the executor's record — the inverse of `order_event_row`.

    Args:
        row: The row as the file holds it

    Returns:
        The event, its unit name dropped and its stamps parsed back into UTC datetimes
    """
    values = row.model_dump(exclude={'scenario_name'})
    for stamp in ('event_time', 'ts_init'):
        moment: Optional[str] = values[stamp]
        values[stamp] = parse_datetime(moment) if moment is not None else None
    return OrderEvent(**values)


def write_order_event_stream(
    io_dir: Path,
    run_id: str,
    units: Iterable[Tuple[str, List[OrderEvent]]],
) -> Optional[Path]:
    """
    Write a backtest's stream at report time — every unit's events, each in its own order.

    Args:
        io_dir: The run's io/ directory
        run_id: The run the stream belongs to
        units: (scenario name, its events) per unit

    Returns:
        The file written, or None when no unit recorded an event
    """
    units = [(name, events) for name, events in units if events]
    if not units:
        return None
    writer = JsonlStreamWriter(Path(io_dir) / ORDER_EVENTS_STREAM)
    try:
        writer.write(order_event_header(run_id))
        for scenario_name, events in units:
            for event in events:
                writer.write(order_event_row(event, scenario_name).model_dump(mode='json'))
    finally:
        writer.close()
    return writer.get_path()


def read_order_event_stream(path: Path) -> Tuple[List[OrderEventRow], bool]:
    """
    Read a stream back.

    A last line without its newline that does not parse is a session killed while it was being
    written: it is left out and reported, everything before it is complete. A broken line
    anywhere else is a damaged file, and a header naming a schema this reader does not know is
    a file it cannot read — both are named as ReportArtifactUnreadableError, as are a line that is
    not a JSON object and a byte that is not UTF-8: the writer escapes everything outside ASCII,
    so neither can be a line cut off mid-write.

    Args:
        path: The stream's file

    Returns:
        (the rows in file order, whether the last line was cut off)
    """
    try:
        raw = Path(path).read_text(encoding='utf-8')
    except UnicodeDecodeError as e:
        raise ReportArtifactUnreadableError(
            ORDER_EVENTS_STREAM, str(path), f'not UTF-8: {e}') from e
    lines = raw.split('\n')
    complete_tail = raw.endswith('\n')
    if complete_tail:
        lines = lines[:-1]
    rows: List[OrderEventRow] = []
    truncated = False
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        is_last = index == len(lines) - 1
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ReportArtifactUnreadableError(
                    ORDER_EVENTS_STREAM, str(path), f'line {index + 1}: not a JSON object')
            if record.get('record_kind') == _HEADER_KIND:
                if record.get('schema_version') != ORDER_EVENTS_SCHEMA_VERSION:
                    raise ReportArtifactUnreadableError(
                        ORDER_EVENTS_STREAM, str(path),
                        f"schema_version {record.get('schema_version')!r}, this reader knows "
                        f'{ORDER_EVENTS_SCHEMA_VERSION}')
                continue
            rows.append(OrderEventRow.model_validate(record))
        except (json.JSONDecodeError, ValidationError) as e:
            if is_last and not complete_tail:
                truncated = True
                continue
            raise ReportArtifactUnreadableError(
                ORDER_EVENTS_STREAM, str(path), f'line {index + 1}: {e}') from e
    return rows, truncated
