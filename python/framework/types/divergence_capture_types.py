"""
FiniexTestingIDE - Divergence Capture Types
What the divergence report reads from a live deployment's records and from a backtest run's (#512),
before anything is derived from it, and how one trading day's order events are counted against the
day's ledger row.

The count declaration lives here rather than beside OrderEventType: that module is part of the
backtest's import closure, which the divergence gate holds unchanged against the release a session
ran, and the report's own declarations stay outside it.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python.framework.types.api.divergence_report_types import (
    CompareWindow,
    DeclaredStop,
    SegmentBoundary,
    SessionEnd,
    VenueAccountChange,
)
from python.framework.types.api.report_types import (
    DataWindow,
    OrderEventRow,
    RunResultRow,
    SessionBookingState,
    WarningsErrorsOutcome,
)
from python.framework.types.api.state_frame_types import StateFrame, StateFrameStreamEnds
from python.framework.types.autotrader_types.tick_stream_event_types import TickStreamOutage
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.run_origin_types import CodeIdentity, CodeStand
from python.framework.types.trading_env_types.order_event_types import (
    ORDER_EVENT_BY_STATUS,
    OrderEventType,
)
from python.framework.types.trading_env_types.trading_env_stats_types import (
    EXECUTION_STATS_FIELD_BY_STATUS,
)
from python.framework.types.venue_history_types import VenueLedgerEntry, VenueTrade

# Which ExecutionStats column counts each event of a day's order-event record — what the record
# is checked against its ledger row by. The endings are derived from the two maps the executor
# books by, so they cannot drift from it; every other member is declared, an uncounted one with
# why. Two events are written without being counted, and the counting says so where it applies:
# - the step after a partial fill — the venue ended the rest of an order whose executed part is
#   already booked as its fill. It is the one ending that carries `cum_lots`.
# - a live close the venue executed for a position no longer held here. Nothing was booked for
#   it and the stream gives it no mark, so a day holding one records more executions than its row
#   counted; the day stays compared and names them as a divergence between the book and the venue.
ORDER_EVENT_COUNT_COLUMN: Dict[OrderEventType, Optional[str]] = {
    OrderEventType.SUBMITTED: 'orders_submitted',
    OrderEventType.ADOPTED: 'orders_adopted',
    # The executed part of an order the venue then ended is booked as an executed row
    OrderEventType.PARTIALLY_FILLED: 'orders_executed',
    OrderEventType.ACCEPTED: None,           # a step of an order counted where it was submitted
    OrderEventType.UNRESOLVED: None,         # a question; the ending that answers it is counted
    OrderEventType.RESOLVED: None,           # names the order; its ending is counted
    OrderEventType.TRIGGERED: None,          # a stop reached its price; its fill is counted
    OrderEventType.MODIFY_REQUESTED: None,   # an amend is no order of its own
    OrderEventType.MODIFIED: None,
    OrderEventType.MODIFY_REJECTED: None,    # refuses the amend, never the order
    OrderEventType.CANCEL_REQUESTED: None,   # the ending that follows is counted
    OrderEventType.CANCEL_DEFERRED: None,
    OrderEventType.CANCEL_REJECTED: None,    # refuses the cancel, never the order
    **{event: EXECUTION_STATS_FIELD_BY_STATUS[status]
       for status, event in ORDER_EVENT_BY_STATUS.items() if event is not None},
}

# The ExecutionStats columns no event counts: a protective close is counted by its cause, which no
# event of the record names.
UNCOMPARED_COUNT_COLUMNS: Tuple[str, ...] = ('sl_tp_triggered',)


@dataclass(frozen=True)
class LiveSessionCapture:
    """
    One session of a live deployment as its records show it — read, nothing derived.

    Args:
        run_id: The session's run identity
        start_time: Its start, from its header as the run index carries it — the first ledger
            row's run timestamp where the index no longer does; None where neither is readable
        rows: Every ledger row naming it, ordered by period number — its sealed trading days and,
            once it closed, the row of its close
        booking_state: Whether its figures exist, and if not, why
        code_stand: Which code it started on, from its rows; None on a session without a booked
            row, and on a row written before the column existed
        broker_type: The broker its rows name; '' on a session without a booked row
        symbol: The symbol it traded, from its rows; None as above
        rendered_config_id: The run-config store id of its rendered configuration; '' as above
        broker_config_id: The run-config store id of its frozen broker configuration; '' as above
        lot_step: The symbol's lot increment in that frozen configuration; None where it could
            not be read
        base_currency: The symbol's base currency in that configuration — the holding a spot
            position is; None as above
        first_tick: Its first tick, from its warmup history; None where the record is missing or
            the tick never came
        warmup_reach: How far back its warmup reached — the longest stretch a timeframe's required
            bars span; None where the record is missing or names no known timeframe
        outcome: How it ended, from its warnings-errors report; None while it runs, and where it
            died before writing one
        frame_ends: Its state-frame stream at both ends; None where it records no frames
        records_pruned_at: When a prune deleted its run directory; None while it was not
        app_version: The release it ran, from its rows; None as above
        rendered_strategy_config: Its strategy as its components were built, every default filled
            in — from its rendered configuration in the run-config store; None where that cannot
            be read
        code_identity: Which code it ran — the framework, the strategy's repository and every
            component's digest — from its header; None where the header cannot be read
        unreadable: What could not be read, by what it is, with the reason — a record named here
            is not checked, never absent
    """
    run_id: str
    start_time: Optional[datetime]
    rows: List[RunResultRow]
    booking_state: SessionBookingState
    code_stand: Optional[CodeStand]
    broker_type: str
    symbol: Optional[str]
    rendered_config_id: str
    broker_config_id: str
    lot_step: Optional[float]
    base_currency: Optional[str]
    first_tick: Optional[datetime]
    outcome: Optional[WarningsErrorsOutcome]
    frame_ends: Optional[StateFrameStreamEnds]
    records_pruned_at: Optional[datetime]
    app_version: Optional[str] = None
    warmup_reach: Optional[timedelta] = None
    rendered_strategy_config: Optional[Dict[str, Any]] = None
    code_identity: Optional[CodeIdentity] = None
    unreadable: Dict[str, str] = field(default_factory=dict)

    def get_began(self) -> Optional[datetime]:
        """
        When the session began, in the time of its events — its first tick, and its start on the
        machine's clock only where no first tick is recorded. The comparison's windows and days
        are event time, so its sessions are too: a replay of recorded data starts on today's clock
        with ticks of another month, and a live session's first tick follows its start by the
        seconds its boot takes.

        Returns:
            The instant; None where neither is known
        """
        return self.first_tick or self.start_time


@dataclass(frozen=True)
class LiveDeploymentCapture:
    """
    Every session of one live deployment, and where its market's trading day flips.

    Args:
        deployment_id: The deployment the sessions name
        broker_type: The broker every booked session names
        anchor: Where that broker's market flips its trading day
        weekend_closure: Whether that market closes — only then are days skipped
        sessions: Every session, booked or not, ordered by start, the run id breaking a tie
    """
    deployment_id: str
    broker_type: str
    anchor: DayAnchorConfig
    weekend_closure: bool
    sessions: List[LiveSessionCapture]


@dataclass(frozen=True)
class BoundaryPosition:
    """
    What an account held at a trading-day boundary, read from the last state frame before it — the
    same reading on the live side and on the backtest's side.

    Args:
        boundary: The boundary
        frame_time: The frame it was read from, the last one strictly before the boundary; None
            where no frame came before it
        net_lots: Lots held per symbol, a long positive and a short negative; empty when flat
        holdings: Every currency held — spot only; None at margin and where no frame was read
    """
    boundary: datetime
    frame_time: Optional[datetime]
    net_lots: Dict[str, float]
    holdings: Optional[Dict[str, float]]


@dataclass(frozen=True)
class LiveDayRecords:
    """
    What one session recorded for one trading day it sealed (#476), read from its day records.

    Args:
        run_id: The session
        trading_day: The day's label
        order_events: The day's order events of this process's own plane, in `seq` order; empty
            where the record is missing or unreadable
        order_events_path: The day record of the order-event stream; None where the session has
            none for the day
        order_events_unreadable: Why that record could not be read; None where it could
        order_events_label: The day its footer names; None where it was not read
        order_events_cut_last_line: Whether a last line cut off mid-write was left out of it
        order_events_last_time: The latest event time it holds, from its footer
        frames_path: The day record of the state frames; None where there is none
        frames_first_time: The first frame's time in it — the day's first pass after its opening
        frames_last_time: The last frame's time in it
        closing_position: What the account held at the day's closing, from the record's last
            frame; None where the day has no frames record
        tick_stream_events_path: The day record of the tick-stream events; None where there is none
    """
    run_id: str
    trading_day: date
    order_events: List[OrderEventRow]
    order_events_path: Optional[Path]
    order_events_unreadable: Optional[str]
    order_events_label: Optional[date]
    order_events_cut_last_line: bool
    order_events_last_time: Optional[datetime]
    frames_path: Optional[Path]
    frames_first_time: Optional[datetime]
    frames_last_time: Optional[datetime]
    closing_position: Optional[BoundaryPosition]
    tick_stream_events_path: Optional[Path]


@dataclass(frozen=True)
class RecordedWindow:
    """
    What a backtest of one segment's window starts from — the session's own records (#512).

    Args:
        warmup_from_run: The segment's first session, whose warmup history the backtest reads
        start: The window's start
        end: The window's end
        balances: What the account held at the segment's first frame, every currency held; None
            where no frame was recorded
        rendered_config_id: The run-config store id of the session's rendered configuration
        broker_config_id: The run-config store id of its frozen broker configuration
    """
    warmup_from_run: str
    start: datetime
    end: datetime
    balances: Optional[Dict[str, float]]
    rendered_config_id: str
    broker_config_id: str


@dataclass(frozen=True)
class DerivedSegment:
    """
    One segment of a deployment as its records place it, before its days are classified.

    Args:
        index: Its position in the deployment, from 1
        started_by: Every reason it begins where it does
        sessions: Its sessions that ran, oldest first
        ends: Where each of them ends, in the same order
        window: The stretch of it that is compared
        declared_stops: The declared stops that fall into it
        venue_account_changes: The movements of the account the venue's ledger shows in it that
            are no order of the bot; None where the ledger was not read
    """
    index: int
    started_by: List[SegmentBoundary]
    sessions: List[LiveSessionCapture]
    ends: List[SessionEnd]
    window: CompareWindow
    declared_stops: List[DeclaredStop]
    venue_account_changes: Optional[List[VenueAccountChange]] = None


@dataclass(frozen=True)
class BacktestScenarioCapture:
    """
    One scenario of a backtest run, as the run recorded it.

    Args:
        name: The scenario
        declared_window: The window its header says it was set to cover; None where the header
            names none for it
        first_tick: The first tick it processed, from its scenario details; None where it
            processed none or the details cannot be read
        last_tick: The last tick it processed; None as above
        records_frames: Whether its state-frame stream exists
        rendered_strategy_config: Its strategy as its components were built, from the run's
            rendered scenario set; None where that cannot be read
    """
    name: str
    declared_window: Optional[DataWindow]
    first_tick: Optional[datetime]
    last_tick: Optional[datetime]
    records_frames: bool
    rendered_strategy_config: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class BacktestRunCapture:
    """
    A finished backtest run as its records show it — what the divergence gate reads of it.

    Args:
        run_id: The run
        app_version: The release it ran, from its header; None where the header cannot be read
        code_identity: Which code it ran, from its header; None as above
        rows: Its ledger rows
        scenarios: Its scenarios, by name
        unreadable: What could not be read, by what it is, with the reason
    """
    run_id: str
    app_version: Optional[str]
    code_identity: Optional[CodeIdentity]
    rows: List[RunResultRow]
    scenarios: List[BacktestScenarioCapture]
    unreadable: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordedFrames:
    """
    One run unit's state frames, read whole — a live session's or a backtest scenario's.

    Args:
        frames: Every frame, in the order written
        unreadable: Why the stream could not be read in full; '' where it could
    """
    frames: List[StateFrame]
    unreadable: str = ''


@dataclass(frozen=True)
class BacktestScenarioRecords:
    """
    What one backtest scenario recorded as it ran — what the measures read of it.

    Args:
        scenario: The scenario
        order_events: Its order events, in `seq` order
        frames: Its state frames
        unreadable: What could not be read, by what it is, with the reason
    """
    scenario: str
    order_events: List[OrderEventRow]
    frames: RecordedFrames
    unreadable: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordedOutages:
    """
    One session's tick-stream outages, read whole.

    Args:
        outages: Every outage, in the order recorded
        unreadable: Why the record could not be read in full; '' where it could
    """
    outages: List[TickStreamOutage]
    unreadable: str = ''


@dataclass
class VenueRunAnswers:
    """
    What the venue's trade list says about each run of missing trade ids.

    Args:
        counts: How many trades the venue published inside each run, by the run's two archive
            trade ids
        unreadable: Each run whose stretch could not be read, with the reason; or why the venue's
            list is not read at all
    """
    counts: Dict[Tuple[int, int], int] = field(default_factory=dict)
    unreadable: List[str] = field(default_factory=list)


@dataclass
class VenueBarTrades:
    """
    The venue's trades of each decision bar a capture difference is proposed for.

    Args:
        trades: The venue's trades, by the bar's opening
        unreadable: Each bar that could not be read, with the reason; or why the venue's list is
            not read at all
    """
    trades: Dict[datetime, List[VenueTrade]] = field(default_factory=dict)
    unreadable: List[str] = field(default_factory=list)


@dataclass
class VenueLedgerRead:
    """
    The account's ledger at its venue over a stretch.

    Args:
        entries: Its entries in booking order; None where it was not read
        unreadable: Why it was not read; '' where it was
    """
    entries: Optional[List[VenueLedgerEntry]] = None
    unreadable: str = ''
