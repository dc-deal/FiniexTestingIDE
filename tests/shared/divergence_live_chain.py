"""
A live deployment for the divergence report's tests, built in memory: sessions with their ledger
rows in the shape the ledger writes them, how each ended, their first ticks and state frames, and
the day records each session sealed — enough to place segments, windows and days without a run
directory. Every session trades one order a sealed day by default, which its row counts; a test
changes what it needs to.

The market is crypto, whose trading day flips at 00:00 UTC, and the deployment starts the way the
thirty-day run does: its first tick five minutes after midnight.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from python.framework.reporting.builders.divergence.live_trading_day_builder import closing_of
from python.framework.types.api.report_types import (
    OpenPositionRow,
    OrderEventRow,
    RunResultRow,
    SessionBookingState,
    WarningsErrorsOutcome,
)
from python.framework.types.api.state_frame_types import (
    StateFrame,
    StateFrameCause,
    StateFrameStreamEnds,
)
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.divergence_capture_types import (
    BoundaryPosition,
    LiveDayRecords,
    LiveDeploymentCapture,
    LiveSessionCapture,
)
from python.framework.types.run_origin_types import FRAMEWORK_REPOSITORY, CodeStand, RepositoryStand
from python.framework.types.run_results_types import PeriodCloseReason
from python.framework.types.trading_env_types.order_event_types import OrderEventType

CRYPTO_ANCHOR = DayAnchorConfig(local_time='00:00', timezone='UTC')
DEPLOYMENT = 'my_deployment'
SYMBOL = 'ETHUSD'
LOT_STEP = 0.01
FIRST_DAY = date(2026, 11, 24)
FIRST_TICK = datetime(2026, 11, 24, 0, 5, tzinfo=timezone.utc)

DayRecords = Dict[Tuple[str, date], LiveDayRecords]


def code_stand(commit: str = 'a1b2c3d4') -> CodeStand:
    """A stand of one repository on one commit."""
    return CodeStand(repositories=[RepositoryStand(
        repository=FRAMEWORK_REPOSITORY, commit=commit, dirty=False,
        recent_commits=[commit])])


def frame(at: datetime, lots: float = 0.0, direction: str = 'long',
          balances: Optional[Dict[str, float]] = None) -> StateFrame:
    """A state frame at an instant, holding one position of `lots` (none when 0)."""
    positions = ([OpenPositionRow(position_id='pos_1', direction=direction, lots=lots,
                                  entry_price=2000.0, entry_time=at.isoformat())] if lots else [])
    return StateFrame(
        frame_time=at, written_at=at, written_for=StateFrameCause.CADENCE, ticks_processed=1,
        symbol=SYMBOL, account_currency='USD', balance=10000.0, balances=balances,
        open_positions=positions)


def event(seq: int, event_type: OrderEventType, at: datetime,
          cum_lots: Optional[float] = None) -> OrderEventRow:
    """One recorded order event of the session's own plane."""
    return OrderEventRow(scenario_name='my_session', seq=seq, event_type=event_type,
                         order_id=f'order_{seq}', submitted_seq=seq, symbol=SYMBOL,
                         lots=0.1, cum_lots=cum_lots, event_time=at.isoformat())


def ledger_row(run_id: str, period_no: int, opened_at: datetime, closed_at: datetime,
               reason: PeriodCloseReason = PeriodCloseReason.ANCHOR,
               counts: Optional[Dict[str, Optional[int]]] = None,
               stand: Optional[CodeStand] = None, pruned_at: str = '') -> RunResultRow:
    """One booking period's ledger row, as the ledger writes it for a live session."""
    counted = {'orders_submitted': 1, 'orders_executed': 1}
    counted.update(counts or {})
    return RunResultRow(
        param_hash='hash', run_id=run_id, run_timestamp=opened_at.isoformat(),
        scenario_set_name='my_bot', deployment_id=DEPLOYMENT, run_type='autotrader',
        data_broker_type='kraken_spot', symbols=[SYMBOL], rendered_config_id='rendered_1',
        broker_config_id='broker_1', code_stand=stand or code_stand(), currency='USD',
        unit_name='my_session', period_no=period_no, period_opened_at=opened_at.isoformat(),
        period_closed_at=closed_at.isoformat(), period_close_reason=reason.value,
        records_pruned_at=pruned_at,
        **{column: counted.get(column, 0) for column in (
            'orders_submitted', 'orders_adopted', 'orders_executed', 'orders_denied',
            'orders_rejected', 'orders_cancelled', 'orders_expired', 'orders_undelivered',
            'orders_unaccounted', 'sl_tp_triggered')})


def day_record(run_id: str, day: date, events: Optional[List[OrderEventRow]] = None,
               lots_at_closing: float = 0.0, **changes) -> LiveDayRecords:
    """
    What a session recorded for one sealed day: by default one order submitted and filled at noon,
    frames from the day's first minute to its last, and the position at its closing.
    """
    closing = closing_of(day, CRYPTO_ANCHOR)
    noon = closing - timedelta(hours=12)
    if events is None:
        events = [event(1, OrderEventType.SUBMITTED, noon), event(2, OrderEventType.FILLED, noon)]
    last_frame = closing - timedelta(minutes=1)
    values = dict(
        run_id=run_id, trading_day=day, order_events=events,
        order_events_path=Path(f'/runs/{run_id}/days/{day:%Y%m%d}/io/order_events.parquet'),
        order_events_unreadable=None, order_events_label=day, order_events_cut_last_line=False,
        order_events_last_time=max((parse(e.event_time) for e in events), default=None),
        frames_path=Path(f'/runs/{run_id}/days/{day:%Y%m%d}/io/state_frames.parquet'),
        frames_first_time=closing - timedelta(days=1) + timedelta(seconds=3),
        frames_last_time=last_frame,
        closing_position=BoundaryPosition(
            boundary=closing, frame_time=last_frame,
            net_lots={SYMBOL: lots_at_closing} if lots_at_closing else {}, holdings=None),
        tick_stream_events_path=None)
    values.update(changes)
    return LiveDayRecords(**values)


def parse(text: Optional[str]) -> Optional[datetime]:
    """An ISO instant back."""
    return datetime.fromisoformat(text) if text else None


def session(run_id: str, first_tick: Optional[datetime], days: Sequence[date],
            ended_at: Optional[datetime] = None, closed: bool = False,
            outcome: Optional[WarningsErrorsOutcome] = None,
            stand: Optional[CodeStand] = None, first_period_no: int = 1,
            first_lots: float = 0.0, last_lots: float = 0.0,
            booking_state: SessionBookingState = SessionBookingState.BOOKED,
            start_time: Optional[datetime] = None,
            row_counts: Optional[Dict[date, Dict[str, Optional[int]]]] = None,
            pruned_at: str = '') -> LiveSessionCapture:
    """
    One session that sealed the given days, each with its ANCHOR row; closed at `ended_at` with a
    session-end row when `closed`, else its last frame there.
    """
    began = start_time or (first_tick - timedelta(seconds=30) if first_tick else None)
    rows: List[RunResultRow] = []
    period_no = first_period_no
    opened = first_tick or began
    for day in days:
        closing = closing_of(day, CRYPTO_ANCHOR)
        rows.append(ledger_row(run_id, period_no, opened, closing,
                               counts=(row_counts or {}).get(day), stand=stand,
                               pruned_at=pruned_at))
        period_no += 1
        opened = closing
    if closed and ended_at is not None:
        rows.append(ledger_row(run_id, period_no, opened, ended_at,
                               reason=PeriodCloseReason.SESSION_END, counts={}, stand=stand))
    last_at = ended_at or (
        closing_of(days[-1], CRYPTO_ANCHOR) + timedelta(hours=1) if days else began)
    ends = StateFrameStreamEnds(
        unit_name='my_session', cadence_timeframe='M1', cadence_s=60.0,
        first=frame(first_tick or began, lots=first_lots), last=frame(last_at, lots=last_lots),
        truncated_tail=False) if (first_tick or began) else None
    if booking_state is not SessionBookingState.BOOKED:
        rows = []
    return LiveSessionCapture(
        run_id=run_id, start_time=began, rows=rows, booking_state=booking_state,
        code_stand=(stand or code_stand()) if rows else None, broker_type='kraken_spot',
        symbol=SYMBOL, rendered_config_id='rendered_1', broker_config_id='broker_1',
        lot_step=LOT_STEP, base_currency='ETH', first_tick=first_tick,
        outcome=outcome or (WarningsErrorsOutcome(shutdown_mode='normal') if closed else None),
        frame_ends=ends, records_pruned_at=None)


def days_from(first: date, count: int) -> List[date]:
    """`count` consecutive labels from a first one."""
    return [first + timedelta(days=offset) for offset in range(count)]


def records_of(capture: LiveSessionCapture, skip: Sequence[date] = (),
               lots: Optional[Dict[date, float]] = None) -> DayRecords:
    """The default day records of every day a session sealed, but those skipped."""
    records: DayRecords = {}
    for row in capture.rows:
        if row.period_close_reason != PeriodCloseReason.ANCHOR.value:
            continue
        day = parse(row.period_closed_at).date() - timedelta(days=1)
        if day in skip:
            continue
        records[(capture.run_id, day)] = day_record(capture.run_id, day,
                                                    lots_at_closing=(lots or {}).get(day, 0.0))
    return records


def deployment(*sessions: LiveSessionCapture) -> LiveDeploymentCapture:
    """The sessions as one deployment on the crypto market."""
    return LiveDeploymentCapture(
        deployment_id=DEPLOYMENT, broker_type='kraken_spot', anchor=CRYPTO_ANCHOR,
        weekend_closure=False, sessions=list(sessions))
