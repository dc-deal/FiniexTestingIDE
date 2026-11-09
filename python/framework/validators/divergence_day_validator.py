"""
FiniexTestingIDE - Divergence Day Validator
Decides which trading days of a live deployment the divergence report compares (#512): each sealed
day's order-event record against its ledger row, every cause that excludes a day, and — once a
backtest of the window exists — the days after a break of the record that may be compared again.

A day is compared only where no cause holds, and every cause that holds is named with what it
found. A missing record is never a pass: what cannot be checked is excluded as not checked.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from python.framework.reporting.builders.divergence.live_trading_day_builder import (
    closed_at_of,
    closing_of,
    count_day_order_events,
    positions_agree,
    row_key_of,
    trading_day_grid,
)
from python.framework.types.api.divergence_report_types import (
    DayCompleteness,
    DayCompletenessState,
    DayCountMismatch,
    DayExclusionReason,
    LiveSegment,
    LiveSide,
    NamedSession,
    SessionEnd,
    TradingDayClassification,
)
from python.framework.types.api.report_types import RunResultRow
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.divergence_capture_types import (
    ORDER_EVENT_COUNT_COLUMN,
    UNCOMPARED_COUNT_COLUMNS,
    BoundaryPosition,
    DerivedSegment,
    LiveDayRecords,
    LiveDeploymentCapture,
    LiveSessionCapture,
)
from python.framework.types.run_results_types import PeriodCloseReason
from python.framework.types.trading_env_types.order_event_types import OrderEventType
from python.framework.types.trading_env_types.trading_env_stats_types import EXECUTION_COUNT_FIELDS
from python.framework.utils.time_utils import parse_iso_datetime
from python.framework.utils.trading_day_anchor import trading_day_of

# The count a fill is counted in — the one a record can hold more of than its row, unlost
_EXECUTED_COLUMN = ORDER_EVENT_COUNT_COLUMN[OrderEventType.FILLED]


def check_day_completeness(row: RunResultRow, records: Optional[LiveDayRecords],
                           closing: datetime) -> DayCompleteness:
    """
    A sealed day's order-event record against the counts of the ledger row that sealed it.

    Args:
        row: The day's ledger row
        records: What the session recorded for the day; None where it holds no record of it
        closing: When the day closed

    Returns:
        Complete, incomplete with every count that disagrees, or not checked with why
    """
    uncompared = list(UNCOMPARED_COUNT_COLUMNS)

    def not_checked(because: str) -> DayCompleteness:
        return DayCompleteness(state=DayCompletenessState.NOT_CHECKED,
                               not_checked_because=because, uncompared_columns=uncompared)

    if row.records_pruned_at:
        return not_checked(f'its records were deleted by a prune at {row.records_pruned_at}')
    if records is None or records.order_events_path is None:
        return not_checked('the session holds no day record of its order events for the day')
    if records.order_events_unreadable is not None:
        return not_checked(f'its order-event day record cannot be read: '
                           f'{records.order_events_unreadable}')
    if records.order_events_label != records.trading_day:
        return not_checked(f'its order-event day record names the day '
                           f'{records.order_events_label} in its footer')
    if records.order_events_cut_last_line:
        return not_checked('its order-event day record lost a last line cut off mid-write')
    if records.order_events_last_time is not None and records.order_events_last_time >= closing:
        return not_checked(
            'its order-event day record holds an event of '
            f'{records.order_events_last_time.isoformat()}, after the day closed — a seal that '
            'failed and was made with a later day')
    compared = [column for column in EXECUTION_COUNT_FIELDS
                if column not in UNCOMPARED_COUNT_COLUMNS]
    missing = [column for column in compared if getattr(row, column) is None]
    if missing:
        return not_checked(f'its ledger row carries no {", ".join(missing)}')
    recorded = count_day_order_events(records.order_events)
    mismatches = [DayCountMismatch(column=column, recorded=recorded[column],
                                   counted=getattr(row, column))
                  for column in compared if recorded[column] != getattr(row, column)]
    # More executions than counted, and nothing else apart: nothing was lost — the venue filled an
    # order for a position the session no longer held, and the book booked nothing for it
    if (len(mismatches) == 1 and mismatches[0].column == _EXECUTED_COLUMN
            and mismatches[0].recorded > mismatches[0].counted):
        return DayCompleteness(
            state=DayCompletenessState.COMPLETE, uncompared_columns=uncompared,
            unbooked_executions=mismatches[0].recorded - mismatches[0].counted)
    return DayCompleteness(
        state=DayCompletenessState.INCOMPLETE if mismatches else DayCompletenessState.COMPLETE,
        mismatches=mismatches, uncompared_columns=uncompared, unbooked_executions=0)


def _alive_spans(segment: DerivedSegment) -> List[Tuple[str, datetime, datetime]]:
    """
    The stretch each session of a segment ran: from its start to where its records end.

    Args:
        segment: The segment

    Returns:
        (the session, from, to) per session whose start is known
    """
    spans: List[Tuple[str, datetime, datetime]] = []
    for session, end in zip(segment.sessions, segment.ends):
        began = session.start_time or session.first_tick
        if began is None:
            continue
        spans.append((session.run_id, began, end.at if end.at is not None else began))
    return spans


def _sealing_rows(
        segment: DerivedSegment) -> Dict[datetime, List[Tuple[LiveSessionCapture, RunResultRow]]]:
    """
    The rows that sealed a trading day at its boundary, by the boundary.

    Args:
        segment: The segment

    Returns:
        Closing instant → every (session, row) that sealed a day there
    """
    by_closing: Dict[datetime, List[Tuple[LiveSessionCapture, RunResultRow]]] = {}
    for session in segment.sessions:
        for row in session.rows:
            closed_at = closed_at_of(row)
            if row.period_close_reason == PeriodCloseReason.ANCHOR.value and closed_at is not None:
                by_closing.setdefault(closed_at, []).append((session, row))
    return by_closing


def _stops_in(ends: Sequence[SessionEnd], opening: datetime,
              closing: datetime) -> List[Tuple[DayExclusionReason, str]]:
    """
    The stops and kills that fall into a day.

    Args:
        ends: Where the segment's sessions end
        opening: When the day opened
        closing: When it closed

    Returns:
        (the cause, what happened) per stop inside the day
    """
    found: List[Tuple[DayExclusionReason, str]] = []
    for end in ends:
        if not (end.kind.is_stop() or end.kind.is_kill()) or end.at is None \
                or not opening <= end.at < closing:
            continue
        reason = (DayExclusionReason.SESSION_STOPPED_IN_DAY if end.kind.is_stop()
                  else DayExclusionReason.SESSION_KILLED_IN_DAY)
        what = f'{end.run_id} ended {end.kind.value} at {end.at.isoformat()}'
        if end.emergency_reason:
            what += f': {end.emergency_reason}'
        found.append((reason, what))
    return found


def _overlaps(spans: Sequence[Tuple[str, datetime, datetime]], opening: datetime,
              closing: datetime) -> List[str]:
    """
    The pairs of sessions that ran at once inside a day.

    Args:
        spans: Each session's stretch
        opening: When the day opened
        closing: When it closed

    Returns:
        One sentence per pair
    """
    found: List[str] = []
    for position, (first_id, first_from, first_to) in enumerate(spans):
        for second_id, second_from, second_to in spans[position + 1:]:
            begins, ends = max(first_from, second_from, opening), min(first_to, second_to, closing)
            if begins < ends:
                found.append(f'{first_id} and {second_id} both ran from {begins.isoformat()} '
                             f'to {ends.isoformat()}')
    return found


def classify_trading_days(segment: DerivedSegment,
                          day_records: Mapping[Tuple[str, date], LiveDayRecords],
                          anchor: DayAnchorConfig,
                          weekend_closure: bool) -> List[TradingDayClassification]:
    """
    Every trading day of a segment, compared or excluded with every cause that holds.

    Args:
        segment: The segment, its window placed
        day_records: What each session recorded per day it sealed, by (session, day)
        anchor: Where the market's day flips
        weekend_closure: Whether the market closes at all

    Returns:
        The days in order — from the window's first day to the last one the segment sealed
    """
    by_closing = _sealing_rows(segment)
    if not by_closing:
        return []
    window = segment.window
    if window.start is None:
        return [_unplaceable_day(closing, rows, day_records, anchor)
                for closing, rows in sorted(by_closing.items())]
    spans = _alive_spans(segment)
    labels = trading_day_grid(window.start, max(by_closing), anchor, weekend_closure)
    days: List[TradingDayClassification] = []
    broken = False
    opening = window.start
    for position, label in enumerate(labels):
        closing = closing_of(label, anchor)
        day = _classify_day(label, position == 0, opening, closing, by_closing.get(closing, []),
                            segment, spans, day_records)
        if broken:
            day.exclusions.append(DayExclusionReason.RESUMPTION_PENDING)
            day.details.append('a day after a break of the record: compared once both sides '
                               'hold the same position at its opening')
        if window.end is not None and closing > window.end:
            day.exclusions.append(DayExclusionReason.AFTER_WINDOW_END)
            day.details.append(f'it closes after the window ends at {window.end.isoformat()} '
                               f'({window.end_cause.value})')
        if any(reason.breaks_the_record() for reason in day.exclusions):
            broken = True
        day.included = not day.exclusions
        days.append(day)
        opening = closing
    return days


def _classify_day(label: date, first_of_segment: bool, opening: datetime, closing: datetime,
                  sealing: Sequence[Tuple[LiveSessionCapture, RunResultRow]],
                  segment: DerivedSegment, spans: Sequence[Tuple[str, datetime, datetime]],
                  day_records: Mapping[Tuple[str, date], LiveDayRecords]
                  ) -> TradingDayClassification:
    """
    One trading day with the causes its own records give — before the window and the record's
    breaks before it are weighed.

    Args:
        label: The day's label
        first_of_segment: Whether it is the segment's first day
        opening: When it opened
        closing: When it closed
        sealing: Every (session, row) that sealed it at its closing
        segment: The segment
        spans: Each session's stretch
        day_records: What each session recorded per day it sealed

    Returns:
        The day, its own causes named
    """
    exclusions: List[DayExclusionReason] = []
    details: List[str] = []
    for reason, what in _stops_in(segment.ends, opening, closing):
        exclusions.append(reason)
        details.append(what)
    if not first_of_segment and not any(began <= opening <= until for _, began, until in spans):
        exclusions.append(DayExclusionReason.NO_SESSION_AT_OPENING)
        details.append(f'no session ran when it opened at {opening.isoformat()}')
    overlaps = _overlaps(spans, opening, closing)
    if overlaps:
        exclusions.append(DayExclusionReason.SESSION_OVERLAP)
        details.extend(overlaps)
    if not sealing:
        exclusions.append(DayExclusionReason.NOT_SEALED_AT_BOUNDARY)
        details.append(f'no session sealed it at {closing.isoformat()}')
        return TradingDayClassification(
            trading_day=label, opening=opening, closing=closing,
            first_of_segment=first_of_segment, exclusions=exclusions, details=details)
    if len(sealing) > 1:
        exclusions.append(DayExclusionReason.DUPLICATE_ROWS)
        details.append('it was sealed more than once: ' + ', '.join(
            f'{row.run_id} period {row.period_no}' for _, row in sealing))
    session, row = sealing[0]
    records = day_records.get((session.run_id, label))
    went_on = _end_of(segment, session.run_id)
    # A loop that did not run leaves no record of any stream; a stream that stopped writing alone
    # leaves the others' records, and the day is then only not checked
    loop_did_not_run = records is None and went_on is not None and went_on > closing
    if loop_did_not_run:
        exclusions.append(DayExclusionReason.LOOP_DID_NOT_RUN)
        details.append(f'{session.run_id} sealed it and went on, but recorded nothing of it — '
                       'its loop did not run through the day')
    completeness = check_day_completeness(row, records, closing)
    if row.records_pruned_at:
        exclusions.append(DayExclusionReason.RECORDS_PRUNED)
        details.append(completeness.not_checked_because)
    elif completeness.state is DayCompletenessState.INCOMPLETE:
        exclusions.append(DayExclusionReason.INCOMPLETE_RECORD)
        details.append('its record and its ledger row disagree: ' + ', '.join(
            f'{mismatch.column} {mismatch.recorded} recorded, {mismatch.counted} counted'
            for mismatch in completeness.mismatches))
    elif completeness.state is DayCompletenessState.NOT_CHECKED and not loop_did_not_run:
        exclusions.append(DayExclusionReason.COMPLETENESS_NOT_CHECKED)
        details.append(completeness.not_checked_because)
    if completeness.unbooked_executions:
        details.append(f'{completeness.unbooked_executions} execution(s) the venue made that the '
                       'book did not book — a divergence between the book and the venue')
    first_frame = records.frames_first_time if records else None
    last_frame = records.frames_last_time if records else None
    return TradingDayClassification(
        trading_day=label, opening=opening, closing=closing, run_id=session.run_id,
        first_of_segment=first_of_segment, exclusions=exclusions, details=details,
        completeness=completeness, ledger_row=row_key_of(row),
        first_frame_after_opening_s=_seconds(opening, first_frame),
        last_frame_before_closing_s=_seconds(last_frame, closing),
        order_events_path=_path_text(records.order_events_path if records else None),
        frames_path=_path_text(records.frames_path if records else None),
        tick_stream_events_path=_path_text(records.tick_stream_events_path if records else None))


def _unplaceable_day(closing: datetime, sealing: Sequence[Tuple[LiveSessionCapture, RunResultRow]],
                     day_records: Mapping[Tuple[str, date], LiveDayRecords],
                     anchor: DayAnchorConfig) -> TradingDayClassification:
    """
    A sealed day of a segment whose first tick is not recorded — listed, never compared.

    Args:
        closing: When the day closed
        sealing: Every (session, row) that sealed it there
        day_records: What each session recorded per day it sealed
        anchor: Where the market's day flips

    Returns:
        The day, not placed in any window
    """
    session, row = sealing[0]
    label = trading_day_of(closing, anchor) - timedelta(days=1)
    opened = parse_iso_datetime(row.period_opened_at) if row.period_opened_at else closing
    return TradingDayClassification(
        trading_day=label, opening=opened, closing=closing, run_id=session.run_id,
        exclusions=[DayExclusionReason.WINDOW_START_UNKNOWN],
        details=["the segment's first tick is not recorded, so its window has no start"],
        completeness=check_day_completeness(row, day_records.get((session.run_id, label)), closing),
        ledger_row=row_key_of(row))


def _end_of(segment: DerivedSegment, run_id: str) -> Optional[datetime]:
    """
    Where one session of a segment ends.

    Args:
        segment: The segment
        run_id: The session

    Returns:
        Its end's instant; None where it is not known
    """
    for session, end in zip(segment.sessions, segment.ends):
        if session.run_id == run_id:
            return end.at
    return None


def _seconds(earlier: Optional[datetime], later: Optional[datetime]) -> Optional[float]:
    """
    The seconds from one instant to another.

    Args:
        earlier: The first instant, or None
        later: The second one, or None

    Returns:
        The seconds; None where either is unknown
    """
    if earlier is None or later is None:
        return None
    return (later - earlier).total_seconds()


def _path_text(path: Optional[Path]) -> Optional[str]:
    """
    A path as the model carries it.

    Args:
        path: The path, or None

    Returns:
        Its text; None where there is none
    """
    return str(path) if path is not None else None


def live_side_of(capture: LiveDeploymentCapture, segments: Sequence[DerivedSegment],
                 named: Sequence[NamedSession],
                 day_records: Mapping[Tuple[str, date], LiveDayRecords],
                 until: Optional[datetime] = None) -> LiveSide:
    """
    A deployment's side of the divergence report: its segments, their windows and every trading
    day of them classified.

    Args:
        capture: The deployment as its records show it
        segments: Its segments, their windows placed
        named: Its sessions that cover no day
        day_records: What each session recorded per day it sealed
        until: The boundary the report was asked for, or None

    Returns:
        The live side
    """
    return LiveSide(
        deployment_id=capture.deployment_id, broker_type=capture.broker_type,
        anchor_timezone=capture.anchor.timezone, anchor_local_time=capture.anchor.local_time,
        until_requested=until, named_sessions=list(named),
        segments=[LiveSegment(
            index=segment.index, started_by=segment.started_by,
            run_ids=[session.run_id for session in segment.sessions], window=segment.window,
            session_ends=segment.ends, declared_stops=segment.declared_stops,
            days=classify_trading_days(segment, day_records, capture.anchor,
                                       capture.weekend_closure))
            for segment in segments])


def closing_positions_of(days: Sequence[TradingDayClassification],
                         day_records: Mapping[Tuple[str, date], LiveDayRecords]
                         ) -> Dict[datetime, BoundaryPosition]:
    """
    What the live account held at each closing a session sealed, from the day's last frame.

    Args:
        days: A segment's days
        day_records: What each session recorded per day it sealed

    Returns:
        Closing instant → the position there; a day without a frames record is absent
    """
    positions: Dict[datetime, BoundaryPosition] = {}
    for day in days:
        records = day_records.get((day.run_id, day.trading_day)) if day.run_id else None
        if records is not None and records.closing_position is not None:
            positions[day.closing] = records.closing_position
    return positions


def resolve_resumption(days: Sequence[TradingDayClassification],
                       live_positions: Mapping[datetime, BoundaryPosition],
                       backtest_positions: Mapping[datetime, BoundaryPosition],
                       lot_step: float,
                       base_currency: Optional[str]) -> List[TradingDayClassification]:
    """
    Decide the days after a break of the record: compared again from the first opening at which
    both sides hold the same position, the days before it apart. The only way a day leaves
    `resumption_pending`.

    Args:
        days: A segment's classified days, in order
        live_positions: What the live account held at each boundary
        backtest_positions: What the backtest's account held at each boundary
        lot_step: The symbol's lot increment — a difference of exactly one step still agrees
        base_currency: The symbol's base currency, compared at spot; None where it is not known

    Returns:
        The days, the pending ones decided
    """
    resolved: List[TradingDayClassification] = []
    resumed = False
    for day in days:
        exclusions = list(day.exclusions)
        details = list(day.details)
        if DayExclusionReason.RESUMPTION_PENDING in exclusions:
            live = live_positions.get(day.opening)
            backtest = backtest_positions.get(day.opening)
            if not resumed and live is not None and backtest is not None and positions_agree(
                    live, backtest, lot_step, base_currency):
                resumed = True
                details.append(f'both sides hold the same position at {day.opening.isoformat()}: '
                               f'compared again from here')
            if resumed:
                exclusions.remove(DayExclusionReason.RESUMPTION_PENDING)
            else:
                exclusions[exclusions.index(DayExclusionReason.RESUMPTION_PENDING)] = \
                    DayExclusionReason.POSITIONS_APART
                details.append(_apart(day.opening, live, backtest))
        if any(reason.breaks_the_record() for reason in exclusions):
            resumed = False
        resolved.append(day.model_copy(update={
            'exclusions': exclusions, 'details': details, 'included': not exclusions}))
    return resolved


def _apart(boundary: datetime, live: Optional[BoundaryPosition],
           backtest: Optional[BoundaryPosition]) -> str:
    """
    Why the two sides count as apart at a boundary.

    Args:
        boundary: The boundary
        live: The live position there, or None
        backtest: The backtest's, or None

    Returns:
        One sentence
    """
    if live is None or live.frame_time is None:
        return f'no live state frame before {boundary.isoformat()} to read the position from'
    if backtest is None or backtest.frame_time is None:
        return f'no backtest state frame before {boundary.isoformat()} to read the position from'
    return (f'the two sides hold different positions at {boundary.isoformat()}: live '
            f'{live.net_lots or "flat"}, backtest {backtest.net_lots or "flat"}')
