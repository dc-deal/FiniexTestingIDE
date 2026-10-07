"""
FiniexTestingIDE - Order-Event Stream IO (#362)

The run's order-event stream on disk: `io/order_events.jsonl`, one transition per line. A header
line comes first and carries the schema version once — DDIA's answer for a file of many records
written by one writer. Every later line is an OrderEventRow or, in a live session, a
BrokerTruthRow — what the venue reported when asked — told apart by `record_plane` and numbered
on one counter. Both are the models the API serves, so the file and the route cannot describe a
line differently.

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
from python.framework.types.api.report_types import (
    BrokerTruthRow,
    OrderEventRow,
    ReconcileDivergenceRow,
    VenueOrderRow,
    VenuePositionRow,
)
from python.framework.types.live_types.broker_truth_types import (
    BrokerTruthRecord,
    BrokerTruthSnapshot,
)
from python.framework.types.live_types.reconciliation_types import (
    BrokerOrder,
    BrokerPosition,
    ReconcileDivergence,
)
from python.framework.types.trading_env_types.order_event_types import (
    OrderEvent,
    OrderEventPlane,
)
from python.framework.utils.time_utils import parse_datetime

ORDER_EVENTS_SCHEMA_VERSION = 2
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


def broker_truth_row(record: BrokerTruthRecord, scenario_name: str) -> BrokerTruthRow:
    """
    One broker-truth record as the row the file holds and the API serves.

    The venue's objects are projected, never copied whole: their raw payload is the adapter's
    and stays there.

    Args:
        record: The executor's record
        scenario_name: The unit it belongs to

    Returns:
        The row
    """
    snapshot = record.snapshot
    return BrokerTruthRow(
        scenario_name=scenario_name,
        seq=record.seq,
        record_plane=record.record_plane,
        read_reason=record.read_reason,
        reconcile_state=record.reconcile_state,
        divergence=(ReconcileDivergenceRow(**asdict(record.divergence))
                    if record.divergence is not None else None),
        venue_orders=([_venue_order_row(order) for order in snapshot.venue_orders]
                      if snapshot.venue_orders is not None else None),
        venue_balances=(dict(snapshot.venue_balances)
                        if snapshot.venue_balances is not None else None),
        venue_positions=([_venue_position_row(position) for position in snapshot.venue_positions]
                         if snapshot.venue_positions is not None else None),
        unread_parts=list(snapshot.unread_parts),
        event_time=record.event_time.isoformat() if record.event_time is not None else None,
        ts_init=record.ts_init.isoformat() if record.ts_init is not None else None,
    )


def _venue_order_row(order: BrokerOrder) -> VenueOrderRow:
    """
    One venue order as served.

    Args:
        order: As the adapter parsed it

    Returns:
        The row — `price` becomes `limit_price`, the price the order would fill at
    """
    return VenueOrderRow(
        broker_ref=order.broker_ref,
        client_order_id=order.client_order_id,
        symbol=order.symbol,
        direction=order.direction,
        order_type=order.order_type,
        lots=order.lots,
        filled_lots=order.filled_lots,
        limit_price=order.price,
        stop_price=order.stop_price,
        status=order.status,
    )


def _venue_position_row(position: BrokerPosition) -> VenuePositionRow:
    """
    One venue position as served.

    Args:
        position: As the adapter parsed it

    Returns:
        The row
    """
    return VenuePositionRow(
        symbol=position.symbol,
        direction=position.direction,
        lots=position.lots,
        entry_price=position.entry_price,
        broker_ref=position.broker_ref,
    )


def broker_truth_from_row(row: BrokerTruthRow) -> BrokerTruthRecord:
    """
    A broker-truth line read back as the executor's record — the inverse of `broker_truth_row`.

    The venue's raw payload was never written, so the read-back objects carry none.

    Args:
        row: The line as the file holds it

    Returns:
        The record, its stamps parsed back into UTC datetimes
    """
    return BrokerTruthRecord(
        seq=row.seq,
        read_reason=row.read_reason,
        snapshot=BrokerTruthSnapshot(
            venue_orders=([BrokerOrder(
                broker_ref=order.broker_ref, symbol=order.symbol, direction=order.direction,
                order_type=order.order_type, lots=order.lots, status=order.status,
                price=order.limit_price, stop_price=order.stop_price,
                filled_lots=order.filled_lots, client_order_id=order.client_order_id)
                for order in row.venue_orders] if row.venue_orders is not None else None),
            venue_balances=(dict(row.venue_balances)
                            if row.venue_balances is not None else None),
            venue_positions=([BrokerPosition(
                symbol=position.symbol, direction=position.direction, lots=position.lots,
                entry_price=position.entry_price, broker_ref=position.broker_ref)
                for position in row.venue_positions]
                if row.venue_positions is not None else None),
            unread_parts=list(row.unread_parts),
        ),
        reconcile_state=row.reconcile_state,
        divergence=(ReconcileDivergence(**row.divergence.model_dump())
                    if row.divergence is not None else None),
        event_time=parse_datetime(row.event_time) if row.event_time is not None else None,
        ts_init=parse_datetime(row.ts_init) if row.ts_init is not None else None,
    )


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


def read_order_event_stream(
    path: Path,
) -> Tuple[List[OrderEventRow], List[BrokerTruthRow], bool]:
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
        (the order events in file order, the broker-truth lines in file order, whether the last
        line was cut off)
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
    truths: List[BrokerTruthRow] = []
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
            if record.get('record_plane') == OrderEventPlane.BROKER_TRUTH.value:
                truths.append(BrokerTruthRow.model_validate(record))
            else:
                rows.append(OrderEventRow.model_validate(record))
        except (json.JSONDecodeError, ValidationError) as e:
            if is_last and not complete_tail:
                truncated = True
                continue
            raise ReportArtifactUnreadableError(
                ORDER_EVENTS_STREAM, str(path), f'line {index + 1}: {e}') from e
    return rows, truths, truncated
