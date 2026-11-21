"""
FiniexTestingIDE - Live Trading Day Builder
The trading days of a live deployment as the divergence report places them (#512): the grid of days
a session books, a day's order events counted the way its ledger row counted them, what an account
held at a boundary, and the pairing of days and orders with a backtest of the same window.

Pure: it reads nothing and no clock. The grid follows the booking period recorder exactly — a day
is labelled by the date it opens, ends where the next label opens, and a market's closed days get
no day — and a test holds the two to each other.
"""

from bisect import bisect_left
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from python.framework.types.api.divergence_report_types import (
    LedgerRowKey,
    LiveSubmission,
    OrderPair,
    PairedDay,
    PairedOrders,
    TradingDayClassification,
)
from python.framework.types.api.report_types import OrderEventRow, RunResultRow
from python.framework.types.api.state_frame_types import StateFrame
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.divergence_capture_types import (
    ORDER_EVENT_COUNT_COLUMN,
    BoundaryPosition,
)
from python.framework.types.trading_env_types.order_event_types import (
    ENDING_EVENT_TYPES,
    OrderEventType,
)
from python.framework.types.trading_env_types.trading_env_stats_types import EXECUTION_COUNT_FIELDS
from python.framework.utils.market_calendar import MarketCalendar
from python.framework.utils.time_utils import parse_iso_datetime
from python.framework.utils.trading_day_anchor import (
    boundary_opening,
    trade_date_of,
    trading_day_of,
)

# How far past a closed day the next trading day is looked for — the booking period recorder's
# own limit, longer than any weekend and holiday together
_TRADING_DAY_SEARCH_LIMIT = 14


def is_trading_day(day: date, anchor: DayAnchorConfig, weekend_closure: bool) -> bool:
    """
    Whether the market trades on the trading day with this label — the recorder's own rule.

    The recorder's copy cannot be shared before the run's verdict: it lies in the backtest's import
    closure, which the divergence gate holds unchanged. A test holds the two equal.

    Args:
        day: The trading day's label
        anchor: Where the market's day flips
        weekend_closure: Whether the market closes at all

    Returns:
        True when the day gets a period
    """
    if not weekend_closure:
        return True
    return MarketCalendar.is_trading_date(trade_date_of(day, anchor))


def first_trading_day_from(day: date, anchor: DayAnchorConfig, weekend_closure: bool) -> date:
    """
    The first trading day at or after a label — the label itself on a market that never closes.

    Args:
        day: Where to start looking
        anchor: Where the market's day flips
        weekend_closure: Whether the market closes at all

    Returns:
        The label of the first day the market trades on
    """
    candidate = day
    for _ in range(_TRADING_DAY_SEARCH_LIMIT):
        if is_trading_day(candidate, anchor, weekend_closure):
            return candidate
        candidate += timedelta(days=1)
    raise ValueError(
        f'No trading day within {_TRADING_DAY_SEARCH_LIMIT} days from {day.isoformat()} — the '
        f'market calendar closes the market for longer than any weekend and holiday together')


def closing_of(day: date, anchor: DayAnchorConfig) -> datetime:
    """
    When a trading day closes: where the next label opens, whether or not the market trades then.

    Args:
        day: The trading day's label
        anchor: Where the market's day flips

    Returns:
        The closing instant, UTC
    """
    return boundary_opening(day + timedelta(days=1), anchor)


def trading_day_grid(start: datetime, last_closing: datetime, anchor: DayAnchorConfig,
                     weekend_closure: bool) -> List[date]:
    """
    Every trading day from the one a start falls in to the last that closes by a boundary.

    Args:
        start: The window's start
        last_closing: The last boundary a day may close at
        anchor: Where the market's day flips
        weekend_closure: Whether the market closes at all

    Returns:
        The days' labels, oldest first; empty where no day closes by the boundary
    """
    days: List[date] = []
    day = first_trading_day_from(trading_day_of(start, anchor), anchor, weekend_closure)
    while closing_of(day, anchor) <= last_closing:
        days.append(day)
        day = first_trading_day_from(day + timedelta(days=1), anchor, weekend_closure)
    return days


def is_step_after_partial_fill(event: OrderEventRow) -> bool:
    """
    Whether an event is the step after a partial fill: the venue ended the rest of an order whose
    executed part is booked as its fill. It is written and never counted — the one ending that
    carries the lots executed before it, wherever that execution was recorded.

    Args:
        event: One recorded order event

    Returns:
        True for such a step
    """
    return (event.event_type in ENDING_EVENT_TYPES
            and event.event_type is not OrderEventType.FILLED
            and event.cum_lots is not None)


def count_day_order_events(events: Sequence[OrderEventRow]) -> Dict[str, int]:
    """
    A day's recorded order events counted the way the executor counted them into the day's row.

    Args:
        events: The day's events of this process's own plane

    Returns:
        Every ExecutionStats column → how many of the events it counts; 0 where none
    """
    counts = {column: 0 for column in EXECUTION_COUNT_FIELDS}
    for event in events:
        column = ORDER_EVENT_COUNT_COLUMN[event.event_type]
        if column is None or is_step_after_partial_fill(event):
            continue
        counts[column] += 1
    return counts


def boundary_position_of(frames: Sequence[StateFrame], boundary: datetime) -> BoundaryPosition:
    """
    What an account held at a boundary, from the last frame strictly before it.

    The frame a boundary's own pass writes comes after it, so the last one before it is the
    account as the day left it — read the same way from a live session's frames and from a
    backtest's.

    Args:
        frames: State frames, ordered by frame time
        boundary: The boundary

    Returns:
        The position; without a frame time, lots or holdings where no frame came before it
    """
    index = bisect_left([frame.frame_time for frame in frames], boundary) - 1
    if index < 0:
        return BoundaryPosition(boundary=boundary, frame_time=None, net_lots={}, holdings=None)
    return position_of_frame(frames[index], boundary)


def position_of_frame(frame: StateFrame, boundary: datetime) -> BoundaryPosition:
    """
    What an account held in one frame, read as its position at a boundary.

    Args:
        frame: The state frame
        boundary: The boundary it stands for

    Returns:
        The position: net lots per symbol, and every currency held at spot
    """
    net_lots: Dict[str, float] = {}
    for position in frame.open_positions:
        signed = position.lots if position.direction == 'long' else -position.lots
        net_lots[frame.symbol] = net_lots.get(frame.symbol, 0.0) + signed
    return BoundaryPosition(
        boundary=boundary, frame_time=frame.frame_time, net_lots=net_lots,
        holdings=dict(frame.balances) if frame.balances is not None else None)


def positions_agree(first: BoundaryPosition, second: BoundaryPosition, lot_step: float,
                    base_currency: Optional[str]) -> bool:
    """
    Whether two accounts held the same position at a boundary, within one lot step — at spot the
    base currency's holding too, which is what a spot position is.

    Args:
        first: One side's position
        second: The other side's
        lot_step: The symbol's lot increment — a difference of exactly one step still agrees
        base_currency: The symbol's base currency; None where it is not known

    Returns:
        True when they agree; never where either side read no frame
    """
    if first.frame_time is None or second.frame_time is None:
        return False
    tolerance = lot_step * (1 + 1e-9)
    for symbol in set(first.net_lots) | set(second.net_lots):
        if abs(first.net_lots.get(symbol, 0.0) - second.net_lots.get(symbol, 0.0)) > tolerance:
            return False
    if first.holdings is None and second.holdings is None:
        return True
    if first.holdings is None or second.holdings is None or base_currency is None:
        return False
    return abs(first.holdings.get(base_currency, 0.0)
               - second.holdings.get(base_currency, 0.0)) <= tolerance


def row_key_of(row: RunResultRow) -> Optional[LedgerRowKey]:
    """
    The key of a booking period's ledger row.

    Args:
        row: A ledger row

    Returns:
        Its key; None on a row that books no period
    """
    if row.period_no is None:
        return None
    return LedgerRowKey(run_id=row.run_id, unit_name=row.unit_name, period_no=row.period_no)


def closed_at_of(row: RunResultRow) -> Optional[datetime]:
    """
    When a ledger row's booking period closed.

    Args:
        row: A ledger row

    Returns:
        The instant; None on a row that books no period
    """
    return parse_iso_datetime(row.period_closed_at) if row.period_closed_at else None


def pair_trading_days(days: Sequence[TradingDayClassification],
                      backtest_rows: Sequence[RunResultRow]) -> List[PairedDay]:
    """
    Pair every compared live day with the backtest row that closed at the same boundary.

    Args:
        days: A segment's trading days
        backtest_rows: The rows of a backtest of the segment's window

    Returns:
        One pair per compared day, in order; a day the backtest closed no row at carries none
    """
    by_closing: Dict[datetime, RunResultRow] = {}
    for row in backtest_rows:
        closed_at = closed_at_of(row)
        if closed_at is not None and row.period_no is not None:
            by_closing.setdefault(closed_at, row)
    paired: List[PairedDay] = []
    for day in days:
        if not day.included or day.ledger_row is None:
            continue
        backtest_row = by_closing.get(day.closing)
        paired.append(PairedDay(
            closing=day.closing, live_row=day.ledger_row,
            backtest_row=row_key_of(backtest_row) if backtest_row is not None else None))
    return paired


def pair_orders_of_day(live_events: Sequence[Tuple[str, OrderEventRow]],
                       backtest_events: Sequence[OrderEventRow]) -> PairedOrders:
    """
    Pair one day's submissions on both sides by their rank — the live ones in the order of their
    sessions, then of their `seq`, the backtest's by `seq`.

    Args:
        live_events: The day's live events, each with the session that recorded it, the sessions
            in the order they ran
        backtest_events: The backtest's events of the same day

    Returns:
        The pairs, and what is left over on each side
    """
    session_rank: Dict[str, int] = {}
    for run_id, _ in live_events:
        session_rank.setdefault(run_id, len(session_rank))
    live = sorted(((run_id, event.seq) for run_id, event in live_events
                   if event.event_type is OrderEventType.SUBMITTED),
                  key=lambda submission: (session_rank[submission[0]], submission[1]))
    backtest = sorted(event.seq for event in backtest_events
                      if event.event_type is OrderEventType.SUBMITTED)
    paired = min(len(live), len(backtest))
    return PairedOrders(
        pairs=[OrderPair(live_run_id=run_id, live_seq=seq, backtest_seq=backtest[rank])
               for rank, (run_id, seq) in enumerate(live[:paired])],
        live_unpaired=[LiveSubmission(run_id=run_id, seq=seq) for run_id, seq in live[paired:]],
        backtest_unpaired=backtest[paired:])
