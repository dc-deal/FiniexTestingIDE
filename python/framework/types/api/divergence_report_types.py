"""
FiniexTestingIDE - Divergence Report Types
The divergence report's model (#512): which stretches of a live deployment are compared with a
backtest of the same window, and which trading days of them, each excluded day with every cause
that holds. What the report measures over those days is added beside it.

A cause names the section of #512's verdict rules it comes from, so a reader of the report can
look the rule up rather than guess it.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel

from python.framework.types.api.report_types import DeploymentCodeChange


class SegmentBoundaryKind(StrEnum):
    """
    What begins a segment — a stretch of a deployment that is compared as one.

    DEPLOYMENT_START: the deployment's first session begins its first segment
    CODE_STAND: the code moved between two sessions — the later one begins a segment of its own
    """
    DEPLOYMENT_START = 'deployment_start'
    CODE_STAND = 'code_stand'


class SegmentBoundary(BaseModel):
    """
    One reason a segment begins where it does.

    Args:
        kind: What begins it
        run_id: The session it begins with
        previous_run_id: The session before it; None at the deployment's start
        code_changes: What moved in the code, one entry per repository that did; a code stand
            only
        stand_unread: Whether a session's code stand could not be read, so the code is not known
            to be the same; a code stand only
    """
    kind: SegmentBoundaryKind
    run_id: str
    previous_run_id: Optional[str] = None
    code_changes: List[DeploymentCodeChange] = []
    stand_unread: bool = False


class SessionEndKind(StrEnum):
    """
    How a session ended, as its records say — never read from a clock, so a session is never
    called running.

    CLOSED: it closed normally
    OPERATOR_INTERRUPTED: an operator stopped it by Ctrl+C
    CRASHED: it ended in an emergency shutdown that reached its cleanup
    REFUSED_AT_FIRST_TICK: it refused at its first tick, its warmup history incomplete
    NO_CLOSE: no row of its close exists and a later session followed — killed, or died before
        its close
    OPEN: the deployment's last session, no row of its close yet — it runs, or it was killed; the
        records cannot tell which
    """
    CLOSED = 'closed'
    OPERATOR_INTERRUPTED = 'operator_interrupted'
    CRASHED = 'crashed'
    REFUSED_AT_FIRST_TICK = 'refused_at_first_tick'
    NO_CLOSE = 'no_close'
    OPEN = 'open'

    def is_stop(self) -> bool:
        """
        Whether a session that ended this way was stopped rather than killed — an end that is
        neither, a session still open, is no break at all.

        Returns:
            True for a normal close and an operator's Ctrl+C
        """
        return self in _STOP_ENDS

    def is_kill(self) -> bool:
        """
        Whether a session that ended this way was killed, crashed or refused — treated as a kill.

        Returns:
            True for an end without its close, an emergency shutdown and a refusal at the first tick
        """
        return self in _KILL_ENDS


_STOP_ENDS = frozenset({SessionEndKind.CLOSED, SessionEndKind.OPERATOR_INTERRUPTED})
_KILL_ENDS = frozenset({SessionEndKind.CRASHED, SessionEndKind.REFUSED_AT_FIRST_TICK,
                        SessionEndKind.NO_CLOSE})


class SessionEnd(BaseModel):
    """
    Where one session's records end.

    Args:
        run_id: The session
        kind: How it ended
        at: When, on the canonical clock — its close row's closing instant, else the last sign of
            life its records hold; None where they hold none
        at_is_last_sign_of_life: Whether `at` is the last sign of life rather than a close
        emergency_reason: The emergency shutdown's cause, quoted from its report; '' otherwise
    """
    run_id: str
    kind: SessionEndKind
    at: Optional[datetime] = None
    at_is_last_sign_of_life: bool = False
    emergency_reason: str = ''


class DeclaredStop(BaseModel):
    """
    A stop of a session as the operator declares it — what `v1.5.0`'s records cannot say.

    Args:
        instant: When the session was stopped
        changed_account: Whether the account was changed while it stood — a sale by hand, a wind
            down, a deposit. Such a stop ends the compared window at the last boundary before it;
            any other stop is treated like a kill
        note: The operator's words
    """
    instant: datetime
    changed_account: bool
    note: str = ''


class WindowEndCause(StrEnum):
    """
    What ends a compared window.

    UNTIL: the boundary the report was asked for
    LAST_SEALED: no boundary was asked for — the last one the deployment sealed
    NOT_REACHED: the boundary asked for is not sealed yet — the window ends at the last sealed one
    DECLARED_STOP: a stop the operator declared as one that changed the account
    UNDECLARED_ACCOUNT_CHANGE: a position or holding moved across a restart and no stop was
        declared — until the operator declares the stop as one that changed nothing
    START_UNKNOWN: the segment's first tick is not recorded, so the window has no start
    """
    UNTIL = 'until'
    LAST_SEALED = 'last_sealed'
    NOT_REACHED = 'not_reached'
    DECLARED_STOP = 'declared_stop'
    UNDECLARED_ACCOUNT_CHANGE = 'undeclared_account_change'
    START_UNKNOWN = 'start_unknown'


class CompareWindow(BaseModel):
    """
    The stretch of a segment that is compared.

    Args:
        start: The segment's first tick — never the first period's opening; None where it is not
            recorded
        end: The boundary the window ends at; None where it has no start or nothing is sealed
        end_cause: What ends it
        until_requested: The boundary the report was asked for; None where none was
        last_sealed: The last boundary the segment sealed; None where it sealed none
        end_detail: Who and when, for an end a stop or an account change set; '' otherwise
    """
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    end_cause: WindowEndCause
    until_requested: Optional[datetime] = None
    last_sealed: Optional[datetime] = None
    end_detail: str = ''


class DayCompletenessState(StrEnum):
    """
    Whether a sealed day's order-event record holds what its ledger row counted.

    COMPLETE: every compared count agrees
    INCOMPLETE: a count disagrees — each is named
    NOT_CHECKED: the check could not be made — the cause is named; never a pass
    """
    COMPLETE = 'complete'
    INCOMPLETE = 'incomplete'
    NOT_CHECKED = 'not_checked'


class DayCountMismatch(BaseModel):
    """
    One count a day's record and its ledger row disagree on.

    Args:
        column: The ExecutionStats column
        recorded: How many of the day's recorded events that column counts
        counted: What the day's ledger row counted
    """
    column: str
    recorded: int
    counted: int


class DayCompleteness(BaseModel):
    """
    A sealed day's order-event record against its ledger row.

    Args:
        state: Complete, incomplete or not checked
        mismatches: Every count that disagrees
        not_checked_because: Why it could not be checked; '' where it was
        uncompared_columns: The counts no event of the record counts, left out of the check
        unbooked_executions: How many executions the record holds beyond what the row counted,
            where nothing else disagrees — fills the venue made that the book did not book, a
            divergence between the two and no loss of the record; None where it was not checked
    """
    state: DayCompletenessState
    mismatches: List[DayCountMismatch] = []
    not_checked_because: str = ''
    uncompared_columns: List[str] = []
    unbooked_executions: Optional[int] = None


class DayExclusionReason(StrEnum):
    """
    Why a trading day is not compared. A day is compared only where no cause holds, and every cause
    that holds is named.

    SESSION_KILLED_IN_DAY: a session was killed, crashed or ended without its close inside the day
    SESSION_STOPPED_IN_DAY: a session was stopped inside the day
    NO_SESSION_AT_OPENING: the day's opening passed while no session ran — the deployment's first
        day excepted
    LOOP_DID_NOT_RUN: a session that went on sealed the day without a record of it — its loop did
        not run through the day (a machine that slept, a frozen process)
    NOT_SEALED_AT_BOUNDARY: no session sealed the day at its closing
    DUPLICATE_ROWS: two rows seal the day at its closing
    SESSION_OVERLAP: two sessions ran at once inside the day
    INCOMPLETE_RECORD: the day's order-event record disagrees with its ledger row
    COMPLETENESS_NOT_CHECKED: the day's order-event record could not be checked against its row
    RECORDS_PRUNED: the day's records were deleted by a prune
    RESUMPTION_PENDING: a day after a break of the record — compared only once both sides hold
        the same position at its opening, which a backtest of the window decides
    POSITIONS_APART: a day after a break whose opening found the two sides holding different
        positions
    AFTER_WINDOW_END: the day closes after the compared window ends
    WINDOW_START_UNKNOWN: the segment's first tick is not recorded, so no day can be placed in a
        window
    """
    SESSION_KILLED_IN_DAY = 'session_killed_in_day'
    SESSION_STOPPED_IN_DAY = 'session_stopped_in_day'
    NO_SESSION_AT_OPENING = 'no_session_at_opening'
    LOOP_DID_NOT_RUN = 'loop_did_not_run'
    NOT_SEALED_AT_BOUNDARY = 'not_sealed_at_boundary'
    DUPLICATE_ROWS = 'duplicate_rows'
    SESSION_OVERLAP = 'session_overlap'
    INCOMPLETE_RECORD = 'incomplete_record'
    COMPLETENESS_NOT_CHECKED = 'completeness_not_checked'
    RECORDS_PRUNED = 'records_pruned'
    RESUMPTION_PENDING = 'resumption_pending'
    POSITIONS_APART = 'positions_apart'
    AFTER_WINDOW_END = 'after_window_end'
    WINDOW_START_UNKNOWN = 'window_start_unknown'

    def rule_section(self) -> str:
        """
        The section of #512's verdict rules this cause comes from.

        Returns:
            The section's number
        """
        return _RULE_SECTION_BY_REASON[self]

    def breaks_the_record(self) -> bool:
        """
        Whether this cause breaks the record, so the days after it are compared only once both
        sides hold the same position again.

        Returns:
            True for a stop, a kill, a day no session opened and a day the loop did not run
        """
        return self in _BREAKS_THE_RECORD


_RULE_SECTION_BY_REASON = {
    DayExclusionReason.SESSION_KILLED_IN_DAY: '5',
    DayExclusionReason.SESSION_STOPPED_IN_DAY: '4',
    DayExclusionReason.NO_SESSION_AT_OPENING: '5',
    DayExclusionReason.LOOP_DID_NOT_RUN: '5',
    DayExclusionReason.NOT_SEALED_AT_BOUNDARY: '4',
    DayExclusionReason.DUPLICATE_ROWS: '0',
    DayExclusionReason.SESSION_OVERLAP: '0',
    DayExclusionReason.INCOMPLETE_RECORD: '5',
    DayExclusionReason.COMPLETENESS_NOT_CHECKED: '5',
    DayExclusionReason.RECORDS_PRUNED: '0',
    DayExclusionReason.RESUMPTION_PENDING: '5',
    DayExclusionReason.POSITIONS_APART: '5',
    DayExclusionReason.AFTER_WINDOW_END: '4',
    DayExclusionReason.WINDOW_START_UNKNOWN: '4',
}

_BREAKS_THE_RECORD = frozenset({
    DayExclusionReason.SESSION_KILLED_IN_DAY,
    DayExclusionReason.SESSION_STOPPED_IN_DAY,
    DayExclusionReason.NO_SESSION_AT_OPENING,
    DayExclusionReason.LOOP_DID_NOT_RUN,
})


class LedgerRowKey(BaseModel):
    """
    What identifies one ledger row of a booking period.

    Args:
        run_id: The run that booked it
        unit_name: The run unit that booked it
        period_no: Its running number inside that unit
    """
    run_id: str
    unit_name: str
    period_no: int


class TradingDayClassification(BaseModel):
    """
    One trading day of a segment, compared or excluded.

    Args:
        trading_day: Its label, the date it opened
        opening: When it opened — the window's start on the segment's first day
        closing: When it closed, where the next label opens — what a backtest's row is paired by
        run_id: The session that sealed it at its closing; None where none did
        first_of_segment: Whether it is the segment's first day, whose decision on the first tick
            is compared
        included: Whether it is compared — only where no cause holds
        exclusions: Every cause that holds
        details: What each cause found — who, when and which figures — and, on a compared day,
            the executions its book did not book
        completeness: Its record against its ledger row; None where no row seals it
        ledger_row: The row that sealed it; None where none did
        first_frame_after_opening_s: Seconds from its opening to its first state frame — when
            its first pass came; None where it has no frames record
        last_frame_before_closing_s: Seconds from its last state frame to its closing; None as
            above
        order_events_path: Its day record of the order-event stream; None where there is none
        frames_path: Its day record of the state frames; None where there is none
        tick_stream_events_path: Its day record of the tick-stream events; None where there is
            none
    """
    trading_day: date
    opening: datetime
    closing: datetime
    run_id: Optional[str] = None
    first_of_segment: bool = False
    included: bool = False
    exclusions: List[DayExclusionReason] = []
    details: List[str] = []
    completeness: Optional[DayCompleteness] = None
    ledger_row: Optional[LedgerRowKey] = None
    first_frame_after_opening_s: Optional[float] = None
    last_frame_before_closing_s: Optional[float] = None
    order_events_path: Optional[str] = None
    frames_path: Optional[str] = None
    tick_stream_events_path: Optional[str] = None


class NamedSession(BaseModel):
    """
    A session of the deployment that covers no day of a segment, and why — named, never dropped.

    Args:
        run_id: The session
        reason: Why it covers none — it failed before it measured anything, refused at its first
            tick, or never saw a tick
    """
    run_id: str
    reason: str


class LiveSegment(BaseModel):
    """
    One segment of a live deployment: its sessions, its window and every trading day of it.

    Args:
        index: Its position in the deployment, from 1
        started_by: Every reason it begins where it does
        run_ids: Its sessions that ran, oldest first
        window: The stretch of it that is compared
        session_ends: Where each of its sessions' records end
        declared_stops: The declared stops that fall into it
        days: Every trading day of it, in order — compared or excluded
    """
    index: int
    started_by: List[SegmentBoundary]
    run_ids: List[str]
    window: CompareWindow
    session_ends: List[SessionEnd] = []
    declared_stops: List[DeclaredStop] = []
    days: List[TradingDayClassification] = []


class LiveSide(BaseModel):
    """
    A live deployment's side of the divergence report.

    Args:
        deployment_id: The deployment
        broker_type: Its broker
        anchor_timezone: The timezone its market's trading day flips in
        anchor_local_time: The local time of day it flips at
        until_requested: The boundary the report was asked for; None where none was
        segments: Its segments, oldest first
        named_sessions: Its sessions that cover no day, with why
    """
    deployment_id: str
    broker_type: str
    anchor_timezone: str
    anchor_local_time: str
    until_requested: Optional[datetime] = None
    segments: List[LiveSegment] = []
    named_sessions: List[NamedSession] = []


class PairedDay(BaseModel):
    """
    A compared live day and the backtest row that closed at the same boundary.

    Args:
        closing: The boundary both closed at
        live_row: The live day's ledger row
        backtest_row: The backtest's row; None where the backtest closed no row there
    """
    closing: datetime
    live_row: LedgerRowKey
    backtest_row: Optional[LedgerRowKey] = None


class OrderPair(BaseModel):
    """
    A live order and the backtest's order paired with it — by their rank in the day.

    Args:
        live_run_id: The session that submitted the live one
        live_seq: The live submission's `seq`
        backtest_seq: The backtest submission's `seq`
    """
    live_run_id: str
    live_seq: int
    backtest_seq: int


class LiveSubmission(BaseModel):
    """
    One live submission, by where it is recorded.

    Args:
        run_id: The session that submitted it
        seq: Its `seq` in that session's record
    """
    run_id: str
    seq: int


class PairedOrders(BaseModel):
    """
    One day's submissions on both sides, paired by their rank — what is left over listed per side,
    never dropped.

    Args:
        pairs: The paired submissions, in order
        live_unpaired: The live submissions without a counterpart
        backtest_unpaired: The backtest submissions without one, by `seq`
    """
    pairs: List[OrderPair] = []
    live_unpaired: List[LiveSubmission] = []
    backtest_unpaired: List[int] = []
