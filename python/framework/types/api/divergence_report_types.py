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
from typing import Dict, List, Optional

from pydantic import BaseModel, JsonValue

from python.framework.types.api.report_types import DeploymentCodeChange
from python.framework.types.decision_logic_types import DecisionLogicAction
from python.framework.types.trading_env_types.broker_types import FeeSource
from python.framework.types.trading_env_types.order_types import OrderSide
from python.framework.types.venue_history_types import VenueLedgerEntryKind


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


class VenueAccountChange(BaseModel):
    """
    A movement of the account's balance in the venue's own ledger that is no order of the bot.

    Args:
        at: When the venue booked it
        kind: What moved the balance
        asset: The currency it moved
        balance_change: What it did to that balance — the amount less the fee
        venue_ref: The venue's reference of the entry
        order_ref: The order a trade's leg belongs to, where the venue named one; None otherwise
        detail: Why it counts, in a few words
    """
    at: datetime
    kind: VenueLedgerEntryKind
    asset: str
    balance_change: float
    venue_ref: str
    order_ref: Optional[str] = None
    detail: str = ''


class WindowEndCause(StrEnum):
    """
    What ends a compared window.

    UNTIL: the boundary the report was asked for
    LAST_SEALED: no boundary was asked for — the last one the deployment sealed
    NOT_REACHED: the boundary asked for is not sealed yet — the window ends at the last sealed one
    DECLARED_STOP: a stop the operator declared as one that changed the account
    UNDECLARED_ACCOUNT_CHANGE: a position or holding moved across a restart and no stop was
        declared — until the operator declares the stop as one that changed nothing
    VENUE_ACCOUNT_CHANGE: the venue's own ledger shows a movement of the account that is no order
        of the bot — a deposit, a withdrawal, a conversion, a trade of another order; where the
        ledger is read it decides instead of the state frames
    START_UNKNOWN: the segment's first tick is not recorded, so the window has no start
    """
    UNTIL = 'until'
    LAST_SEALED = 'last_sealed'
    NOT_REACHED = 'not_reached'
    DECLARED_STOP = 'declared_stop'
    UNDECLARED_ACCOUNT_CHANGE = 'undeclared_account_change'
    VENUE_ACCOUNT_CHANGE = 'venue_account_change'
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
        venue_account_changes: Every movement of the account the venue's ledger shows in it that
            is no order of the bot; None where the ledger was not read — the state frames then
            judge a restart
    """
    index: int
    started_by: List[SegmentBoundary]
    run_ids: List[str]
    window: CompareWindow
    session_ends: List[SessionEnd] = []
    declared_stops: List[DeclaredStop] = []
    days: List[TradingDayClassification] = []
    venue_account_changes: Optional[List[VenueAccountChange]] = None


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


class OrderOutcome(BaseModel):
    """
    What one run's record holds for one order: its submission, its fills, and what they cost.

    Args:
        run_id: The run that submitted it — the session, or the backtest
        seq: Its submission's `seq` in that run's record
        order_side: Whether it bought or sold — a close sells what a long held; None where its
            record names no action or direction
        submitted_at: When it was submitted, on the run's own clock
        submission_mid: The mid of the quote it was submitted at; None where no tick was in scope
        decision_mid: The mid of the frame of the pass that sent it; None where no frame names it
        decision_equity: The account value in that frame; None as above, and where it is unvalued
        filled_lots: What was executed of it; 0 where nothing was
        fill_price: The lot-weighted mean price of its fills; None where nothing was executed
        filled_at: When its last fill came; None as above
        fee: The fees of its fills, in the account currency; None as above
        fee_source: Whether the fee is the venue's own figure; None as above
        refusal: Why it was refused, where a refusal is recorded — its reason and the venue's
            code; '' where none is
    """
    run_id: str
    seq: int
    order_side: Optional[OrderSide] = None
    submitted_at: Optional[datetime] = None
    submission_mid: Optional[float] = None
    decision_mid: Optional[float] = None
    decision_equity: Optional[float] = None
    filled_lots: float = 0.0
    fill_price: Optional[float] = None
    filled_at: Optional[datetime] = None
    fee: Optional[float] = None
    fee_source: Optional[FeeSource] = None
    refusal: str = ''


class OrderDivergence(BaseModel):
    """
    One order submitted on both sides, measured (measure 2). Every figure is live minus backtest
    where it compares the two; None where an input it needs is missing.

    Args:
        live: The live order
        backtest: The backtest's order
        lots_difference: Filled lots, live minus backtest (2a)
        execution_difference: (fill − submission mid) signed by the order's side, live minus
            backtest — positive where live paid more than the backtest (2b)
        execution_difference_pct: The same in % of the live submission mid
        live_fee_rate_pct: The live fee ÷ (lots × fill price), in % (2c); None where the fee is
            not the venue's own figure
        taker_rate_pct: The frozen broker configuration's taker rate, in %
        fee_not_checked_because: Why the fee rate was not compared; '' where it was
        lots_ratio: Live lots ÷ backtest lots (2d)
        equity_ratio: Live ÷ backtest account value at the two decision frames
        live_delay: (submission mid − decision mid) signed by the side, live — reported apart
        backtest_delay: The same, backtest
    """
    live: OrderOutcome
    backtest: OrderOutcome
    lots_difference: float
    execution_difference: Optional[float] = None
    execution_difference_pct: Optional[float] = None
    live_fee_rate_pct: Optional[float] = None
    taker_rate_pct: Optional[float] = None
    fee_not_checked_because: str = ''
    lots_ratio: Optional[float] = None
    equity_ratio: Optional[float] = None
    live_delay: Optional[float] = None
    backtest_delay: Optional[float] = None


class DayOrderDivergence(BaseModel):
    """
    One compared trading day's orders on both sides (measure 2).

    Args:
        trading_day: The day's label
        closing: When it closed
        orders: The orders submitted on both sides, paired by rank
        live_one_sided: The live orders without a counterpart
        backtest_one_sided: The backtest's orders without one
        live_fill_count: How many live orders executed anything
        backtest_fill_count: How many backtest orders did
        unbooked_executions: Executions the live record holds that its book did not book; None
            where the day's record was not checked
        orders_in_gap: The orders, on either side, submitted or filled inside a gap — named and
            left out of the measure
    """
    trading_day: date
    closing: datetime
    orders: List[OrderDivergence] = []
    live_one_sided: List[OrderOutcome] = []
    backtest_one_sided: List[OrderOutcome] = []
    live_fill_count: int = 0
    backtest_fill_count: int = 0
    unbooked_executions: Optional[int] = None
    orders_in_gap: List[OrderOutcome] = []


class EquityGap(BaseModel):
    """
    The two runs' account values at one instant (measure 3).

    Args:
        at: The cadence slot or boundary they are compared at
        live_equity: The live account value
        backtest_equity: The backtest's
        gap: Live minus backtest
    """
    at: datetime
    live_equity: float
    backtest_equity: float
    gap: float


class DayEquityDifference(BaseModel):
    """
    One compared day's change of account value on both sides (measure 3).

    Args:
        trading_day: The day's label
        closing: When it closed
        live_opening_equity: The live account value at the last frame before the opening; None
            where there is no valued frame
        live_closing_equity: The same before the closing
        backtest_opening_equity: The backtest's, as above
        backtest_closing_equity: The backtest's, as above
        live_change_pct: The live change in % of its opening value
        backtest_change_pct: The backtest's
        difference_pct: Live minus backtest
        closing_gap: The closing values, live minus backtest
        live_ledger_final_equity: The day's live ledger row's final equity, beside the frame's —
            the row values at the period's last tick, the frame at the pass before the closing;
            None where the row holds none
        backtest_ledger_final_equity: The same, the backtest's row
    """
    trading_day: date
    closing: datetime
    live_opening_equity: Optional[float] = None
    live_closing_equity: Optional[float] = None
    backtest_opening_equity: Optional[float] = None
    backtest_closing_equity: Optional[float] = None
    live_change_pct: Optional[float] = None
    backtest_change_pct: Optional[float] = None
    difference_pct: Optional[float] = None
    closing_gap: Optional[float] = None
    live_ledger_final_equity: Optional[float] = None
    backtest_ledger_final_equity: Optional[float] = None


class DailyDifferenceStatistics(BaseModel):
    """
    The days' differences of equity change, as a series (measure 3) — reported, never binding.

    Args:
        day_count: How many days carry a difference
        mean_pct: Their mean
        tracking_error_pct: Their standard deviation; None below two days
        interval_low_pct: The lower end of a 90 % interval on the mean, paired by day
        interval_high_pct: Its upper end
        lag_one_autocorrelation: Whether one day's difference follows the day before's; None below
            three days
        ljung_box_statistic: The Ljung-Box statistic at lag 1
        interval_too_narrow: Whether the test rejects independent days at 5 % — then the interval
            assumes more than the days show; None where it was not computed
    """
    day_count: int = 0
    mean_pct: Optional[float] = None
    tracking_error_pct: Optional[float] = None
    interval_low_pct: Optional[float] = None
    interval_high_pct: Optional[float] = None
    lag_one_autocorrelation: Optional[float] = None
    ljung_box_statistic: Optional[float] = None
    interval_too_narrow: Optional[bool] = None


class EquityTracking(BaseModel):
    """
    How far the two runs' account values ran apart over a segment (measure 3).

    Args:
        shared_slots: How many cadence slots both sides valued, outside the gaps
        slots_in_gaps: How many shared slots a gap left out
        largest_gap: The slot with the largest gap; None where no slot is shared
        gap_at_end: The last shared slot's gap
        days: Every compared day
        daily: The days' differences as a series
    """
    shared_slots: int = 0
    slots_in_gaps: int = 0
    largest_gap: Optional[EquityGap] = None
    gap_at_end: Optional[EquityGap] = None
    days: List[DayEquityDifference] = []
    daily: DailyDifferenceStatistics = DailyDifferenceStatistics()


class AttributionParts(BaseModel):
    """
    One day's change of account value taken apart — for one run, or live minus backtest. The
    parts add up to the change; the residual is what they leave.

    Args:
        equity_change: The change from the last frame before the opening to the last frame
            before the closing
        held: What the account held at the start, valued over the day
        delay: The move from each paired order's decision mid to its submission mid
        execution: Each paired fill against its submission mid
        end_price: Each paired fill's lots valued from its decision mid to the day's end
        one_sided: The whole contribution of the fills of orders the other run did not submit
        fees: The fees of the day's fills, as a contribution (negative)
        valuation: What the account values beside its quote balance and its base holding at the
            mid — another currency it holds; 0 where it holds none
        residual: The change less every part
    """
    equity_change: float
    held: float
    delay: float
    execution: float
    end_price: float
    one_sided: float
    fees: float
    valuation: float
    residual: float


class DayAttribution(BaseModel):
    """
    Where one compared day's difference of equity change comes from.

    Args:
        trading_day: The day's label
        closing: When it closed
        live: The live day taken apart; None where it could not be
        backtest: The backtest's day; None as above
        difference: Live minus backtest, part by part — its residual is the attribution's
        not_computed_because: Why it could not be taken apart; '' where it was
    """
    trading_day: date
    closing: datetime
    live: Optional[AttributionParts] = None
    backtest: Optional[AttributionParts] = None
    difference: Optional[AttributionParts] = None
    not_computed_because: str = ''


class ExplainedFlipKind(StrEnum):
    """
    Which of the four explained kinds a one-sided decision is proposed as — a proposal for a person
    to check, never the verdict.

    LIVE_BREAKER: the live circuit breaker held back an order the backtest sent — a backtest has no
        breaker
    REWARMUP: the day lies within the warmup's reach after a restart, where the session warmed up on
        the venue's bars while the backtest ran on, and the workers' outputs differ
    CAPTURE: the workers' outputs differ with no restart in reach — the decision bar's values differ
        between the session's ticks and the archive's; unconfirmed until the venue's own trades
        show it
    ACCOUNT_VALUE: the workers' outputs agree and the two account values differ — the input that
        changed the action is the account value the fills already moved
    """
    LIVE_BREAKER = 'live_breaker'
    REWARMUP = 'rewarmup'
    CAPTURE = 'capture'
    ACCOUNT_VALUE = 'account_value'


class DecisionComparisonState(StrEnum):
    """
    How one compared day's two decisions relate.

    AGREE: the same action and the same number of orders
    ONE_SIDED: a different action, or an order one side sent and the other did not
    NOT_CHECKED: a side has no frame of its decision — never counted as agreeing
    IN_GAP: its boundary lies in a gap, or its pass came more than the gap bound after the
        boundary, on either side — named and left out with its orders
    """
    AGREE = 'agree'
    ONE_SIDED = 'one_sided'
    NOT_CHECKED = 'not_checked'
    IN_GAP = 'in_gap'


class DayDecision(BaseModel):
    """
    One run's decision of one day: the frame of the pass in which the decision bar closed — on a
    segment's first day, the pass of its first tick.

    Args:
        frame_time: When that pass ran
        action: What it decided; None where the frame names no decision
        order_count: How many orders the run submitted that day
        equity: The account value in the frame; None where it is unvalued
        safety_blocked: Whether the live circuit breaker was holding — always False in a backtest
        worker_outputs: Every worker's outputs the decision read
        decision_outputs: What the decision logic reported beside its action
        decision_bar_open: When the decision bar that closed in the pass opened; None on a
            segment's first day, decided at the first tick, and where no such bar closed
    """
    frame_time: datetime
    action: Optional[DecisionLogicAction] = None
    order_count: int = 0
    equity: Optional[float] = None
    safety_blocked: bool = False
    worker_outputs: Dict[str, Dict[str, JsonValue]] = {}
    decision_outputs: Dict[str, JsonValue] = {}
    decision_bar_open: Optional[datetime] = None


class DecisionComparison(BaseModel):
    """
    One compared day's decision on both sides (measure 1).

    Args:
        trading_day: The day's label
        closing: When it closed
        state: Whether the two decisions agree
        live: The session's decision; None where no frame of it was found
        backtest: The backtest's decision; None as above
        proposed_kind: The explained kind a one-sided day is proposed as; None where none fits — the
            day is then unexplained
        detail: The day, the time and the figures behind the proposal, or why it was not checked
        capture_confirmed: For a day proposed as a capture, whether the venue's trades in the
            decision bar show trades our archive lacks; None where that was not checked
        capture_missing_trades: How many of the venue's trades in the decision bar the archive
            lacks; None where that was not checked
    """
    trading_day: date
    closing: datetime
    state: DecisionComparisonState
    live: Optional[DayDecision] = None
    backtest: Optional[DayDecision] = None
    proposed_kind: Optional[ExplainedFlipKind] = None
    detail: str = ''
    capture_confirmed: Optional[bool] = None
    capture_missing_trades: Optional[int] = None


class DecisionTableCell(BaseModel):
    """
    How many compared days the session decided one action and the backtest another.

    Args:
        live_action: The session's action
        backtest_action: The backtest's
        days: How many days
    """
    live_action: DecisionLogicAction
    backtest_action: DecisionLogicAction
    days: int


class DecisionTable(BaseModel):
    """
    The compared days, live action × backtest action — so the days with a trade cannot hide behind
    the many on which neither side acted.

    Args:
        cells: Every pair of actions that occurred, with its days
        not_checked: The days a side's decision was not found on
        in_gap: The days whose decision a gap left out
    """
    cells: List[DecisionTableCell] = []
    not_checked: int = 0
    in_gap: int = 0


class GapSource(StrEnum):
    """
    Where a gap was found — a stretch in which one side did not see what the other saw.

    ARCHIVE_BARS: more than the gap bound between two consecutive M1 bars of the archive
    TRADE_ID_RUN: a run of the venue's trade ids the archive's ticks lack, spanning more than the
        gap bound
    TICK_STREAM_OUTAGE: an outage the session's tick stream recorded
    FRAME_PAUSE: more than the gap bound between two consecutive state frames of a session — a
        frozen process or a sleeping host records no outage, so only the frames show it
    """
    ARCHIVE_BARS = 'archive_bars'
    TRADE_ID_RUN = 'trade_id_run'
    TICK_STREAM_OUTAGE = 'tick_stream_outage'
    FRAME_PAUSE = 'frame_pause'


class DivergenceGap(BaseModel):
    """
    One gap of a segment's window.

    Args:
        source: Where it was found
        start: Where it begins
        end: Where it ends
        run_id: The session it was found in; '' for the archive
        confirmed: Whether it counts as a gap — a run of missing trade ids counts only once the
            venue's own trade list confirms it; every other source always counts
        detail: What was found, in a few words
        trade_id_before: For a run of missing trade ids, the archive's last trade before it
        trade_id_after: For such a run, the archive's first trade after it
        venue_trades: For such a run, how many trades the venue published between the two;
            None where its list was not read. Zero means the venue allocated those ids without a
            public trade — no gap
    """
    source: GapSource
    start: datetime
    end: datetime
    run_id: str = ''
    confirmed: bool = True
    detail: str = ''
    trade_id_before: Optional[int] = None
    trade_id_after: Optional[int] = None
    venue_trades: Optional[int] = None


class ExcludedSpan(BaseModel):
    """
    A stretch whose minutes are left out on both sides — one or more confirmed gaps, merged.

    Args:
        start: Where it begins
        end: Where it ends
    """
    start: datetime
    end: datetime


class GapList(BaseModel):
    """
    Every gap of a segment's window, and the stretches they leave out.

    Args:
        gaps: Every gap that counts, in time order
        unconfirmed: The runs of missing trade ids that wait for the venue's trade list
        refuted: The runs of missing trade ids the venue's list shows without a single trade —
            ids it allocated and never published; they leave nothing out
        excluded: The confirmed gaps merged into stretches, in time order
        unreadable: What could not be read, with the reason
    """
    gaps: List[DivergenceGap] = []
    unconfirmed: List[DivergenceGap] = []
    refuted: List[DivergenceGap] = []
    excluded: List[ExcludedSpan] = []
    unreadable: List[str] = []


class RenderStamp(BaseModel):
    """
    One bar file a backtest read, and when it was rendered.

    Args:
        timeframe: Its timeframe
        bar_file: The file, by name
        rendered_at: When it was rendered, as its metadata says; None where it says nothing
    """
    timeframe: str
    bar_file: str
    rendered_at: Optional[str] = None


class SegmentMeasures(BaseModel):
    """
    What one segment's compared days show beside a backtest of its window — measure 2, measure 3
    and the day attribution. Numbers only: whether they lie within their bounds is the verdict's.

    Args:
        segment_index: The segment's position in its deployment, from 1
        scenario: The backtest scenario the segment was held against
        days: The segment's days, the ones after a break decided by the resumption rule
        paired_days: Every compared day with the backtest row that closed at the same boundary
        gaps: The window's gaps and the stretches they leave out
        render_stamps: The bar files the backtest read, and when each was rendered
        decisions: Each compared day's decision on both sides (measure 1); empty where no decision
            timeframe was given
        decision_table: The compared days, live action × backtest action
        orders: Each compared day's orders on both sides
        equity: How far the two account values ran apart
        attribution: Each compared day's difference of equity change, taken apart
        unreadable: What could not be read, on either side, with the reason
    """
    segment_index: int
    scenario: str
    days: List[TradingDayClassification] = []
    paired_days: List[PairedDay] = []
    gaps: GapList = GapList()
    render_stamps: List[RenderStamp] = []
    decisions: List[DecisionComparison] = []
    decision_table: DecisionTable = DecisionTable()
    orders: List[DayOrderDivergence] = []
    equity: EquityTracking = EquityTracking()
    attribution: List[DayAttribution] = []
    unreadable: List[str] = []
