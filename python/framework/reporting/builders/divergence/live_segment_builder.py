"""
FiniexTestingIDE - Live Segment Builder
Cuts a live deployment into the segments the divergence report compares one by one (#512), says
where each session's records end, and places each segment's compared window: from its first tick to
the boundary asked for, or to the last boundary before a stop that changed the account.

Pure: it reads nothing and no clock, so a sealed day never depends on whether a session runs now.
What begins a segment is a list of detectors, one per kind of boundary — a later boundary kind
adds one entry and changes nothing else.
"""

from datetime import datetime
from typing import Callable, List, Optional, Sequence, Tuple

from python.framework.reporting.builders.code_stand_comparison import (
    code_changes_between,
    stands_agree,
)
from python.framework.reporting.builders.divergence.live_trading_day_builder import (
    closed_at_of,
    closing_of,
    position_of_frame,
    positions_agree,
    trading_day_grid,
)
from python.framework.reporting.store.run_completion_audit import closes_its_run
from python.framework.types.api.divergence_report_types import (
    CompareWindow,
    DeclaredStop,
    NamedSession,
    SegmentBoundary,
    SegmentBoundaryKind,
    SessionEnd,
    SessionEndKind,
    WindowEndCause,
)
from python.framework.types.api.report_types import SessionBookingState
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.divergence_capture_types import (
    DerivedSegment,
    LiveDeploymentCapture,
    LiveSessionCapture,
    RecordedWindow,
)
from python.framework.types.run_results_types import PeriodCloseReason
from python.framework.utils.trading_day_anchor import boundary_opening, trading_day_of

SegmentBoundaryDetector = Callable[[LiveSessionCapture, LiveSessionCapture],
                                   Optional[SegmentBoundary]]
SegmentBoundaryDetectors = Sequence[Tuple[SegmentBoundaryKind, SegmentBoundaryDetector]]


def code_stand_boundary(previous: LiveSessionCapture,
                        current: LiveSessionCapture) -> Optional[SegmentBoundary]:
    """
    Whether the code moved between two booked sessions; a stand nobody could read counts as moved.

    Args:
        previous: The earlier session
        current: The later one

    Returns:
        A code-stand boundary, or None where both started on the same code
    """
    if (previous.code_stand is None or current.code_stand is None
            or previous.code_stand.is_unread() or current.code_stand.is_unread()):
        return SegmentBoundary(kind=SegmentBoundaryKind.CODE_STAND, run_id=current.run_id,
                               previous_run_id=previous.run_id, stand_unread=True)
    if stands_agree(previous.code_stand, current.code_stand):
        return None
    return SegmentBoundary(
        kind=SegmentBoundaryKind.CODE_STAND, run_id=current.run_id,
        previous_run_id=previous.run_id,
        code_changes=code_changes_between(previous.code_stand, current.code_stand))


# Every kind of boundary that begins a segment, each with its detector. A detector is handed two
# booked sessions in a row and answers with a boundary or None; every boundary found begins a
# segment, and the segment names them all.
SEGMENT_BOUNDARY_DETECTORS: Tuple[Tuple[SegmentBoundaryKind, SegmentBoundaryDetector], ...] = (
    (SegmentBoundaryKind.CODE_STAND, code_stand_boundary),
)


def sessions_that_ran(
    sessions: Sequence[LiveSessionCapture],
) -> Tuple[List[LiveSessionCapture], List[NamedSession]]:
    """
    Split a deployment's sessions into those that ran and those that cover no day — the second
    named with why, never dropped.

    Args:
        sessions: Every session, ordered by start

    Returns:
        (the sessions that ran, in order; the sessions that cover no day, with why)
    """
    ran: List[LiveSessionCapture] = []
    named: List[NamedSession] = []
    for session in sessions:
        if session.booking_state is SessionBookingState.FAILED:
            named.append(NamedSession(run_id=session.run_id,
                                      reason='it failed before it measured anything'))
        elif session.outcome is not None and session.outcome.refused_at_first_tick:
            named.append(NamedSession(run_id=session.run_id,
                                      reason='it refused at its first tick'))
        elif (session.booking_state is SessionBookingState.NOT_BOOKED_YET
              and session.first_tick is None
              and (session.frame_ends is None or session.frame_ends.first is None)):
            named.append(NamedSession(run_id=session.run_id,
                                      reason='it recorded no tick and no state frame'))
        else:
            ran.append(session)
    return ran, named


def cut_segments(
    sessions: Sequence[LiveSessionCapture],
    detectors: SegmentBoundaryDetectors = SEGMENT_BOUNDARY_DETECTORS,
) -> List[Tuple[List[SegmentBoundary], List[LiveSessionCapture]]]:
    """
    Cut sessions into segments: every boundary a detector finds between two booked sessions in a
    row begins one. A session without booked figures never cuts — it joins the segment it ran in.

    Args:
        sessions: The sessions that ran, oldest first
        detectors: The boundary kinds and their detectors

    Returns:
        One (boundaries that began it, its sessions) per segment, oldest first
    """
    segments: List[Tuple[List[SegmentBoundary], List[LiveSessionCapture]]] = []
    previous_booked: Optional[LiveSessionCapture] = None
    for session in sessions:
        boundaries: List[SegmentBoundary] = []
        if not segments:
            boundaries.append(SegmentBoundary(kind=SegmentBoundaryKind.DEPLOYMENT_START,
                                              run_id=session.run_id))
        elif session.booking_state is SessionBookingState.BOOKED and previous_booked is not None:
            for _, detect in detectors:
                found = detect(previous_booked, session)
                if found is not None:
                    boundaries.append(found)
        if boundaries:
            segments.append((boundaries, [session]))
        else:
            segments[-1][1].append(session)
        if session.booking_state is SessionBookingState.BOOKED:
            previous_booked = session
    return segments


def last_sign_of_life(session: LiveSessionCapture) -> Optional[datetime]:
    """
    The latest instant a session's records show it alive: its last state frame, its last closed
    period, its first tick.

    Args:
        session: The session

    Returns:
        The instant on the canonical clock; None where its records hold none
    """
    instants = [closed_at_of(row) for row in session.rows]
    if session.frame_ends is not None and session.frame_ends.last is not None:
        instants.append(session.frame_ends.last.frame_time)
    instants.append(session.first_tick)
    known = [instant for instant in instants if instant is not None]
    return max(known) if known else None


def session_end_of(session: LiveSessionCapture, has_successor: bool) -> SessionEnd:
    """
    Where a session's records end, and how — from its close row and its report, never a clock.

    Args:
        session: The session
        has_successor: Whether a later session of the deployment started

    Returns:
        Its end
    """
    close_row = next((row for row in session.rows if closes_its_run(row.period_close_reason)), None)
    if close_row is None:
        return SessionEnd(
            run_id=session.run_id,
            kind=SessionEndKind.NO_CLOSE if has_successor else SessionEndKind.OPEN,
            at=last_sign_of_life(session), at_is_last_sign_of_life=True)
    outcome = session.outcome
    kind = SessionEndKind.CLOSED
    emergency_reason = ''
    if outcome is not None:
        if outcome.operator_interrupted:
            kind = SessionEndKind.OPERATOR_INTERRUPTED
        elif outcome.refused_at_first_tick:
            kind = SessionEndKind.REFUSED_AT_FIRST_TICK
        elif outcome.emergency_reason or outcome.shutdown_mode == 'emergency':
            kind = SessionEndKind.CRASHED
            emergency_reason = outcome.emergency_reason
    closed_at = closed_at_of(close_row)
    return SessionEnd(
        run_id=session.run_id, kind=kind,
        at=closed_at if closed_at is not None else last_sign_of_life(session),
        at_is_last_sign_of_life=closed_at is None, emergency_reason=emergency_reason)


def last_boundary_at_or_before(instant: datetime, start: datetime, anchor: DayAnchorConfig,
                               weekend_closure: bool) -> datetime:
    """
    The last boundary a trading day of a window closed at, at or before an instant.

    Args:
        instant: The instant
        start: The window's start
        anchor: Where the market's day flips
        weekend_closure: Whether the market closes at all

    Returns:
        That boundary — the window's start where no day of it closed by the instant
    """
    days = trading_day_grid(start, instant, anchor, weekend_closure)
    return closing_of(days[-1], anchor) if days else start


def check_until(until: Optional[datetime], anchor: DayAnchorConfig) -> None:
    """
    Refuse a requested end that is no trading-day boundary — a window ends where a day ends.

    Args:
        until: The boundary asked for, or None
        anchor: Where the market's day flips
    """
    if until is None:
        return
    if boundary_opening(trading_day_of(until, anchor), anchor) != until:
        raise ValueError(
            f'The window can end only at a trading-day boundary ({anchor.local_time} '
            f'{anchor.timezone}); {until.isoformat()} is none.')


def _account_change_end(sessions: Sequence[LiveSessionCapture], ends: Sequence[SessionEnd],
                        declared: Sequence[DeclaredStop], start: datetime, anchor: DayAnchorConfig,
                        weekend_closure: bool) -> Optional[Tuple[datetime, WindowEndCause, str]]:
    """
    The earliest end a stop sets: a declared stop that changed the account, or — where no stop is
    declared across a restart — a position or holding that moved across it.

    Args:
        sessions: The segment's sessions, oldest first
        ends: Where each ends
        declared: The declared stops that fall into the segment
        start: The window's start
        anchor: Where the market's day flips
        weekend_closure: Whether the market closes at all

    Returns:
        (the boundary the window ends at, why, who and when), or None where no stop ends it
    """
    found: List[Tuple[datetime, WindowEndCause, str]] = []
    for stop in declared:
        if stop.changed_account:
            found.append((last_boundary_at_or_before(stop.instant, start, anchor, weekend_closure),
                          WindowEndCause.DECLARED_STOP,
                          f'declared stop at {stop.instant.isoformat()}'
                          + (f': {stop.note}' if stop.note else '')))
    for position in range(len(sessions) - 1):
        stopped, restarted = sessions[position], sessions[position + 1]
        if any(_covers(stop, stopped, restarted) for stop in declared):
            continue
        stopped_at = ends[position].at or restarted.start_time or start
        last = stopped.frame_ends.last if stopped.frame_ends is not None else None
        first = restarted.frame_ends.first if restarted.frame_ends is not None else None
        lot_step = (restarted.lot_step if restarted.lot_step is not None
                    else stopped.lot_step or 0.0)
        base_currency = restarted.base_currency or stopped.base_currency
        if (last is not None and first is not None and positions_agree(
                position_of_frame(last, stopped_at), position_of_frame(first, stopped_at),
                lot_step, base_currency)):
            continue
        found.append((
            last_boundary_at_or_before(stopped_at, start, anchor, weekend_closure),
            WindowEndCause.UNDECLARED_ACCOUNT_CHANGE,
            (f'{stopped.run_id} → {restarted.run_id}: '
             + ('the account held another position after the restart'
                if last is not None and first is not None
                else 'no state frame on one side to compare the account across the restart'))))
    return min(found, key=lambda entry: entry[0]) if found else None


def _covers(stop: DeclaredStop, stopped: LiveSessionCapture, restarted: LiveSessionCapture) -> bool:
    """
    Whether a declared stop is the one between two sessions — it falls from the first one's start
    to the second one's.

    Args:
        stop: The declared stop
        stopped: The session before the restart
        restarted: The session after it

    Returns:
        True where it falls between them
    """
    if stopped.start_time is None or restarted.start_time is None:
        return False
    return stopped.start_time <= stop.instant < restarted.start_time


def segment_window(sessions: Sequence[LiveSessionCapture], ends: Sequence[SessionEnd],
                   declared: Sequence[DeclaredStop], until: Optional[datetime],
                   anchor: DayAnchorConfig, weekend_closure: bool) -> CompareWindow:
    """
    The compared window of one segment.

    Args:
        sessions: The segment's sessions, oldest first
        ends: Where each ends
        declared: The declared stops that fall into the segment
        until: The boundary asked for, or None
        anchor: Where the market's day flips
        weekend_closure: Whether the market closes at all

    Returns:
        The window: its start, its end and what ends it
    """
    sealed = [closed_at_of(row) for session in sessions for row in session.rows
              if row.period_close_reason == PeriodCloseReason.ANCHOR.value]
    known = [instant for instant in sealed if instant is not None]
    last_sealed = max(known) if known else None
    start = sessions[0].first_tick
    if start is None:
        return CompareWindow(start=None, end=None, end_cause=WindowEndCause.START_UNKNOWN,
                             until_requested=until, last_sealed=last_sealed)
    stop_end = _account_change_end(sessions, ends, declared, start, anchor, weekend_closure)
    if stop_end is not None and (until is None or stop_end[0] <= until):
        end, cause, detail = stop_end
        return CompareWindow(start=start, end=end, end_cause=cause, until_requested=until,
                             last_sealed=last_sealed, end_detail=detail)
    if until is None:
        return CompareWindow(start=start, end=last_sealed, end_cause=WindowEndCause.LAST_SEALED,
                             last_sealed=last_sealed)
    if last_sealed is not None and last_sealed >= until:
        return CompareWindow(start=start, end=until, end_cause=WindowEndCause.UNTIL,
                             until_requested=until, last_sealed=last_sealed)
    return CompareWindow(start=start, end=last_sealed, end_cause=WindowEndCause.NOT_REACHED,
                         until_requested=until, last_sealed=last_sealed)


def derive_segments(
    capture: LiveDeploymentCapture,
    until: Optional[datetime] = None,
    declared_stops: Sequence[DeclaredStop] = (),
    detectors: SegmentBoundaryDetectors = SEGMENT_BOUNDARY_DETECTORS,
) -> Tuple[List[DerivedSegment], List[NamedSession]]:
    """
    A deployment's segments, each with where its sessions end and its compared window.

    Args:
        capture: The deployment's sessions as their records show them
        until: The boundary the report is asked for; None for the last one sealed
        declared_stops: The stops the operator declared
        detectors: The boundary kinds and their detectors

    Returns:
        (the segments, oldest first; the sessions that cover no day, with why)
    """
    check_until(until, capture.anchor)
    ran, named = sessions_that_ran(capture.sessions)
    order = {session.run_id: position for position, session in enumerate(capture.sessions)}
    last_position = len(capture.sessions) - 1
    cut = cut_segments(ran, detectors)
    segments: List[DerivedSegment] = []
    for index, (started_by, sessions) in enumerate(cut, start=1):
        ends = [session_end_of(session, has_successor=order[session.run_id] < last_position)
                for session in sessions]
        segment_start = sessions[0].start_time or sessions[0].first_tick
        next_start = cut[index][1][0].start_time if index < len(cut) else None
        declared = [stop for stop in declared_stops
                    if (segment_start is None or stop.instant >= segment_start)
                    and (next_start is None or stop.instant < next_start)]
        segments.append(DerivedSegment(
            index=index, started_by=started_by, sessions=list(sessions), ends=ends,
            window=segment_window(sessions, ends, declared, until, capture.anchor,
                                  capture.weekend_closure),
            declared_stops=declared))
    return segments, named


def recorded_window_of(segment: DerivedSegment) -> RecordedWindow:
    """
    What a backtest of a segment's window starts from: its first session's warmup history and
    first frame, the session's configurations, the window's two ends.

    Args:
        segment: The segment

    Returns:
        The window's inputs of record
    """
    first = segment.sessions[0]
    window = segment.window
    missing = [name for name, value in (
        ('the window start', window.start), ('the window end', window.end),
        ('the rendered configuration', first.rendered_config_id),
        ('the frozen broker configuration', first.broker_config_id)) if not value]
    if missing:
        raise ValueError(
            f'Segment {segment.index} ({first.run_id}) cannot be replayed: its records lack '
            f'{", ".join(missing)}.')
    frame = first.frame_ends.first if first.frame_ends is not None else None
    balances = None
    if frame is not None:
        balances = (dict(frame.balances) if frame.balances is not None
                    else {frame.account_currency: frame.balance})
    return RecordedWindow(
        warmup_from_run=first.run_id, start=window.start, end=window.end, balances=balances,
        rendered_config_id=first.rendered_config_id, broker_config_id=first.broker_config_id)
