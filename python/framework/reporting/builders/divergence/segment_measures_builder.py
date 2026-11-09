"""
The measures of one segment of the divergence report (#512): measure 2, measure 3 and the day
attribution, beside a backtest of the segment's window.

It runs the resumption rule first — the days after a break are compared again from the first
opening at which both sides hold the same position, which the backtest's frames now tell — then
pairs every compared day with the backtest row that closed at the same boundary and measures it.
Pure: it reads the records the two collectors hand it.
"""

from datetime import date, datetime
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from python.framework.reporting.builders.divergence.day_attribution_builder import (
    OrderKey,
    attribute_day,
)
from python.framework.reporting.builders.divergence.equity_divergence_builder import (
    track_equity,
)
from python.framework.reporting.builders.divergence.live_trading_day_builder import (
    boundary_position_of,
    closed_at_of,
    pair_trading_days,
)
from python.framework.reporting.builders.divergence.order_divergence_builder import (
    measure_day_orders,
)
from python.framework.types.api.divergence_report_types import (
    DayAttribution,
    DayOrderDivergence,
    LiveSegment,
    OrderOutcome,
    SegmentMeasures,
)
from python.framework.types.api.report_types import OrderEventRow, RunResultRow
from python.framework.types.divergence_capture_types import (
    BacktestRunCapture,
    BacktestScenarioRecords,
    LiveDayRecords,
    LiveDeploymentCapture,
    RecordedFrames,
)
from python.framework.utils.time_utils import parse_iso_datetime
from python.framework.validators.divergence_day_validator import (
    closing_positions_of,
    resolve_resumption,
)


def measure_segment(segment: LiveSegment, capture: LiveDeploymentCapture,
                    day_records: Mapping[Tuple[str, date], LiveDayRecords],
                    live_frames: Mapping[str, RecordedFrames], backtest: BacktestRunCapture,
                    records: BacktestScenarioRecords,
                    taker_rate_pct: Optional[float]) -> SegmentMeasures:
    """
    One segment's compared days, measured against a backtest of its window.

    Args:
        segment: The segment as the live side placed and classified it
        capture: The deployment's sessions
        day_records: What each session recorded per day it sealed
        live_frames: Each session's state frames
        backtest: The backtest run of the segment's window
        records: What its scenario recorded as it ran
        taker_rate_pct: The frozen broker configuration's taker rate, in percent

    Returns:
        The segment's days after the resumption rule, and measure 2, measure 3 and the day
        attribution of every compared day
    """
    sessions = [session for session in capture.sessions if session.run_id in segment.run_ids]
    first = sessions[0] if sessions else None
    lot_step = first.lot_step if first is not None else None
    base_currency = first.base_currency if first is not None else None
    unreadable = _unreadable_of(segment.run_ids, live_frames, records)
    merged_live = sorted((frame for run_id in segment.run_ids
                          for frame in live_frames.get(run_id, RecordedFrames([])).frames),
                         key=lambda frame: frame.frame_time)
    backtest_frames = records.frames.frames
    days = list(segment.days)
    if lot_step is not None:
        days = resolve_resumption(
            days, closing_positions_of(days, day_records),
            {day.opening: boundary_position_of(backtest_frames, day.opening) for day in days},
            lot_step, base_currency)
    backtest_rows = [row for row in backtest.rows
                     if row.unit_name == records.scenario and row.period_no is not None]
    paired_days = pair_trading_days(days, backtest_rows)
    live_rows = _rows_by_closing(row for session in sessions for row in session.rows)
    backtest_by_closing = _rows_by_closing(backtest_rows)
    live_events = [(day.run_id, event) for day in days if day.run_id
                   for event in _day_events(day_records, day.run_id, day.trading_day)]
    backtest_events = [(backtest.run_id, event) for event in records.order_events]
    orders: List[DayOrderDivergence] = []
    attribution: List[DayAttribution] = []
    for day in days:
        if not day.included or day.run_id is None:
            continue
        day_live = [(day.run_id, event)
                    for event in _day_events(day_records, day.run_id, day.trading_day)]
        day_backtest = [event for event in records.order_events
                        if _within(event, day.opening, day.closing)]
        measured = measure_day_orders(
            day.trading_day, day.closing, day_live,
            {run_id: live_frames.get(run_id, RecordedFrames([])).frames
             for run_id in segment.run_ids},
            backtest.run_id, day_backtest, backtest_frames, taker_rate_pct,
            day.completeness.unbooked_executions if day.completeness is not None else None)
        orders.append(measured)
        attribution.append(attribute_day(
            day.trading_day, day.opening, day.closing,
            merged_live, live_events, _paired_live(measured),
            backtest_frames, backtest_events, _paired_backtest(measured),
            base_currency))
    return SegmentMeasures(
        segment_index=segment.index, scenario=records.scenario, days=days,
        paired_days=paired_days, orders=orders,
        equity=track_equity(days, merged_live, backtest_frames, live_rows, backtest_by_closing),
        attribution=attribution, unreadable=unreadable)


def _day_events(day_records: Mapping[Tuple[str, date], LiveDayRecords], run_id: str,
                trading_day: date) -> List[OrderEventRow]:
    """
    The order events a session recorded for one day.

    Args:
        day_records: What each session recorded per day it sealed
        run_id: The session
        trading_day: The day

    Returns:
        The day's events; empty where the session holds no record of it
    """
    records = day_records.get((run_id, trading_day))
    return list(records.order_events) if records is not None else []


def _within(event: OrderEventRow, opening: datetime, closing: datetime) -> bool:
    """
    Whether an event happened inside a day.

    Args:
        event: The event
        opening: When the day opened
        closing: When it closed

    Returns:
        True for an event at or after the opening and before the closing
    """
    return bool(event.event_time) and opening <= parse_iso_datetime(event.event_time) < closing


def _paired_live(measured: DayOrderDivergence) -> Dict[OrderKey, OrderOutcome]:
    """
    The day's live orders the backtest also submitted.

    Args:
        measured: The day's measured orders

    Returns:
        (session, seq) → the live order
    """
    return {(order.live.run_id, order.live.seq): order.live for order in measured.orders}


def _paired_backtest(measured: DayOrderDivergence) -> Dict[OrderKey, OrderOutcome]:
    """
    The day's backtest orders the session also submitted.

    Args:
        measured: The day's measured orders

    Returns:
        (run, seq) → the backtest's order
    """
    return {(order.backtest.run_id, order.backtest.seq): order.backtest
            for order in measured.orders}


def _rows_by_closing(rows: Iterable[RunResultRow]) -> Dict[datetime, RunResultRow]:
    """
    Booking-period rows by the boundary they closed at.

    Args:
        rows: Ledger rows

    Returns:
        Closing → row; a row that closed no period is left out
    """
    by_closing: Dict[datetime, RunResultRow] = {}
    for row in rows:
        closed = closed_at_of(row)
        if closed is not None:
            by_closing[closed] = row
    return by_closing


def _unreadable_of(run_ids: Sequence[str], live_frames: Mapping[str, RecordedFrames],
                   records: BacktestScenarioRecords) -> List[str]:
    """
    What could not be read on either side, in a few words each.

    Args:
        run_ids: The segment's sessions
        live_frames: Each session's frames
        records: The backtest scenario's records

    Returns:
        One line per record that could not be read in full
    """
    notes = [f'{run_id} state frames: {live_frames[run_id].unreadable}'
             for run_id in run_ids if run_id in live_frames and live_frames[run_id].unreadable]
    notes += [f'{run_id}: no state frames were read' for run_id in run_ids
              if run_id not in live_frames]
    if records.frames.unreadable:
        notes.append(f'backtest {records.scenario} state frames: {records.frames.unreadable}')
    notes += [f'backtest {records.scenario} {name}: {why}'
              for name, why in records.unreadable.items()]
    return notes

