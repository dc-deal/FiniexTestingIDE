"""
Measure 2 of the divergence report (#512) — each order submitted on both sides, measured.

For every compared trading day the submissions of both runs are paired by rank, and each pair is
measured: the lots filled (2a), the execution against the mid the order was submitted at (2b), the
live fee rate against the frozen taker rate (2c), and the size against the two account values at
the decision (2d). The move from the decision's mid to the submission's is reported apart. Pure:
it reads the records it is handed and decides nothing — whether a figure lies within its bound is
the verdict's question.
"""

import math
from bisect import bisect_left
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from python.framework.reporting.builders.divergence.gap_list_builder import in_excluded
from python.framework.reporting.builders.divergence.live_trading_day_builder import (
    pair_orders_of_day,
)
from python.framework.types.api.divergence_report_types import (
    DayOrderDivergence,
    ExcludedSpan,
    OrderDivergence,
    OrderOutcome,
)
from python.framework.types.api.report_types import OrderEventRow
from python.framework.types.api.state_frame_types import StateFrame, StateFrameCause
from python.framework.types.trading_env_types.broker_types import FeeSource
from python.framework.types.trading_env_types.order_event_types import OrderEventType
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderSide,
)
from python.framework.utils.time_utils import parse_iso_datetime

# The events that execute part or all of an order
FILL_EVENT_TYPES = frozenset({OrderEventType.FILLED, OrderEventType.PARTIALLY_FILLED})

# The events that refuse an order the run submitted
_REFUSAL_EVENT_TYPES = frozenset({OrderEventType.REJECTED})


def taker_rate_pct_of(broker_document: Mapping[str, Any]) -> Optional[float]:
    """
    The taker rate a frozen broker configuration declares, in percent.

    Args:
        broker_document: The frozen broker configuration

    Returns:
        The rate (0.8 means 0.80 %); None where it declares no maker-taker fee
    """
    fee_structure = broker_document.get('fee_structure')
    if not isinstance(fee_structure, dict):
        return None
    rate = fee_structure.get('taker_fee')
    return float(rate) if isinstance(rate, (int, float)) else None


def measured_latency_of(days: Sequence[DayOrderDivergence]
                        ) -> Tuple[Optional[Tuple[int, int]], int]:
    """
    The latency the live orders of the compared days measured — from submission to their last
    fill, on the session's clock — as the span the simulator's order latency is drawn from.

    Args:
        days: The compared days' orders, those in a gap already left out

    Returns:
        ((the least, the most) in whole milliseconds, how many orders measured it); (None, 0)
        where no live order filled
    """
    measured = [(order.filled_at - order.submitted_at).total_seconds() * 1000.0
                for day in days
                for order in [pair.live for pair in day.orders] + list(day.live_one_sided)
                if order.filled_lots and order.submitted_at is not None
                and order.filled_at is not None]
    if not measured:
        return None, 0
    return (max(0, math.floor(min(measured))), max(0, math.ceil(max(measured)))), len(measured)


def measure_day_orders(trading_day: date, closing: datetime,
                       live_events: Sequence[Tuple[str, OrderEventRow]],
                       live_frames: Mapping[str, Sequence[StateFrame]],
                       backtest_run_id: str, backtest_events: Sequence[OrderEventRow],
                       backtest_frames: Sequence[StateFrame], taker_rate_pct: Optional[float],
                       unbooked_executions: Optional[int]) -> DayOrderDivergence:
    """
    One compared day's orders on both sides, measured.

    Args:
        trading_day: The day's label
        closing: When it closed
        live_events: The day's live events, each with the session that recorded it, the sessions
            in the order they ran
        live_frames: Each session's state frames, in the order written
        backtest_run_id: The backtest run
        backtest_events: The backtest's events of the same day
        backtest_frames: The backtest scenario's state frames
        taker_rate_pct: The frozen taker rate in percent; None where none is declared
        unbooked_executions: What the live day's completeness check counted

    Returns:
        The day's orders, paired and measured, and what is left over on each side
    """
    pairs = pair_orders_of_day(live_events, backtest_events)
    live_by_run: Dict[str, List[OrderEventRow]] = {}
    for run_id, event in live_events:
        live_by_run.setdefault(run_id, []).append(event)
    live_outcomes = {(run_id, event.seq): outcome_of(run_id, event.seq, events,
                                                     live_frames.get(run_id, ()))
                     for run_id, events in live_by_run.items() for event in events
                     if event.event_type is OrderEventType.SUBMITTED}
    backtest_outcomes = {event.seq: outcome_of(backtest_run_id, event.seq, backtest_events,
                                               backtest_frames)
                         for event in backtest_events
                         if event.event_type is OrderEventType.SUBMITTED}
    orders = [divergence_of(live_outcomes[(pair.live_run_id, pair.live_seq)],
                            backtest_outcomes[pair.backtest_seq], taker_rate_pct)
              for pair in pairs.pairs]
    return DayOrderDivergence(
        trading_day=trading_day, closing=closing, orders=orders,
        live_one_sided=[live_outcomes[(submission.run_id, submission.seq)]
                        for submission in pairs.live_unpaired],
        backtest_one_sided=[backtest_outcomes[seq] for seq in pairs.backtest_unpaired],
        live_fill_count=sum(1 for outcome in live_outcomes.values() if outcome.filled_lots > 0),
        backtest_fill_count=sum(1 for outcome in backtest_outcomes.values()
                                if outcome.filled_lots > 0),
        unbooked_executions=unbooked_executions)


def leave_out_gaps(day: DayOrderDivergence,
                   excluded: Sequence[ExcludedSpan]) -> DayOrderDivergence:
    """
    A day's orders without those submitted or filled inside a gap — named, never dropped.

    Args:
        day: The day's measured orders
        excluded: The stretches the window's gaps leave out

    Returns:
        The day with every such order moved to its orders in a gap, a pair with both its sides
    """
    if not excluded:
        return day

    def in_gap(outcome: OrderOutcome) -> bool:
        return (in_excluded(outcome.submitted_at, excluded)
                or in_excluded(outcome.filled_at, excluded))

    in_gaps: List[OrderOutcome] = []
    orders: List[OrderDivergence] = []
    for order in day.orders:
        if in_gap(order.live) or in_gap(order.backtest):
            in_gaps += [order.live, order.backtest]
        else:
            orders.append(order)
    live = [outcome for outcome in day.live_one_sided if not in_gap(outcome)]
    backtest = [outcome for outcome in day.backtest_one_sided if not in_gap(outcome)]
    in_gaps += [outcome for outcome in day.live_one_sided + day.backtest_one_sided
                if in_gap(outcome)]
    return day.model_copy(update={'orders': orders, 'live_one_sided': live,
                                  'backtest_one_sided': backtest, 'orders_in_gap': in_gaps})


def outcome_of(run_id: str, seq: int, events: Sequence[OrderEventRow],
               frames: Sequence[StateFrame]) -> OrderOutcome:
    """
    What one run's record holds for one order: its submission, its fills and its refusal.

    Args:
        run_id: The run that submitted it
        seq: Its submission's `seq`
        events: The run's events that may belong to it — a fill or a refusal names the
            submission by `submitted_seq`
        frames: The run's state frames, in the order written

    Returns:
        The order's outcome
    """
    submission = next(event for event in events if event.seq == seq)
    fills = [event for event in events
             if event.submitted_seq == seq and event.event_type in FILL_EVENT_TYPES
             and event.lots]
    refusals = [event for event in events
                if event.submitted_seq == seq and event.event_type in _REFUSAL_EVENT_TYPES]
    submitted_at = _instant(submission.event_time)
    decision = decision_frame_of(submission.order_id, submitted_at, frames)
    filled_lots = sum(event.lots for event in fills)
    priced = [event for event in fills if event.fill_price is not None]
    priced_lots = sum(event.lots for event in priced)
    return OrderOutcome(
        run_id=run_id, seq=seq, order_side=order_side_of(submission),
        submitted_at=submitted_at, submission_mid=submission.submission_mid,
        decision_mid=decision.last_price if decision is not None else None,
        decision_equity=decision.equity if decision is not None else None,
        filled_lots=filled_lots,
        fill_price=(sum(event.lots * event.fill_price for event in priced) / priced_lots
                    if priced_lots > 0 else None),
        filled_at=max((_instant(event.event_time) for event in fills
                       if event.event_time), default=None),
        fee=sum(event.fee for event in fills if event.fee is not None) if fills else None,
        fee_source=_fee_source_of(fills),
        refusal='; '.join(_refusal_text(event) for event in refusals))


def divergence_of(live: OrderOutcome, backtest: OrderOutcome,
                  taker_rate_pct: Optional[float]) -> OrderDivergence:
    """
    One order submitted on both sides, measured.

    Args:
        live: The live order
        backtest: The backtest's order
        taker_rate_pct: The frozen taker rate in percent; None where none is declared

    Returns:
        The measures of 2a–2d and the delay
    """
    live_execution = _signed(live.order_side, live.fill_price, live.submission_mid)
    backtest_execution = _signed(backtest.order_side, backtest.fill_price,
                                 backtest.submission_mid)
    execution = (live_execution - backtest_execution
                 if live_execution is not None and backtest_execution is not None else None)
    fee_rate, not_checked = _live_fee_rate(live)
    return OrderDivergence(
        live=live, backtest=backtest,
        lots_difference=live.filled_lots - backtest.filled_lots,
        execution_difference=execution,
        execution_difference_pct=(execution / live.submission_mid * 100.0
                                  if execution is not None and live.submission_mid else None),
        live_fee_rate_pct=fee_rate,
        taker_rate_pct=taker_rate_pct,
        fee_not_checked_because=not_checked if fee_rate is None else (
            '' if taker_rate_pct is not None
            else 'the frozen broker configuration declares no taker rate'),
        lots_ratio=(live.filled_lots / backtest.filled_lots
                    if backtest.filled_lots > 0 else None),
        equity_ratio=(live.decision_equity / backtest.decision_equity
                      if live.decision_equity is not None and backtest.decision_equity
                      else None),
        live_delay=_signed(live.order_side, live.submission_mid, live.decision_mid),
        backtest_delay=_signed(backtest.order_side, backtest.submission_mid,
                               backtest.decision_mid))


def order_side_of(submission: OrderEventRow) -> Optional[OrderSide]:
    """
    Whether a submitted order bought or sold — its record names the POSITION's direction.

    Args:
        submission: The order's submission event

    Returns:
        Buy or sell; None where the record names no action or direction
    """
    if submission.action is None or submission.direction is None:
        return None
    long_position = submission.direction is OrderDirection.LONG
    opening = submission.action is OrderAction.OPEN
    return OrderSide.BUY if long_position == opening else OrderSide.SELL


def decision_frame_of(order_id: str, submitted_at: Optional[datetime],
                      frames: Sequence[StateFrame]) -> Optional[StateFrame]:
    """
    The frame of the pass that sent an order: the one naming it, else the order frame written at
    the instant it was submitted — the pass that decides also submits.

    Args:
        order_id: The order's id in its record
        submitted_at: When it was submitted, on the run's clock
        frames: The run's state frames, in the order written

    Returns:
        The frame; None where no frame is the pass's
    """
    for frame in frames:
        if frame.order_id == order_id:
            return frame
    if submitted_at is None:
        return None
    times = [frame.frame_time for frame in frames]
    index = bisect_left(times, submitted_at)
    while index < len(frames) and frames[index].frame_time == submitted_at:
        if frames[index].written_for is StateFrameCause.ORDER:
            return frames[index]
        index += 1
    return None


def _signed(order_side: Optional[OrderSide], price: Optional[float],
            reference: Optional[float]) -> Optional[float]:
    """
    A price against a reference, signed so that positive means the order paid more.

    Args:
        order_side: The order's side
        price: The price paid or met
        reference: What it is held against

    Returns:
        (price − reference) for a buy, (reference − price) for a sell; None where an input is
        missing
    """
    if order_side is None or price is None or reference is None:
        return None
    return price - reference if order_side is OrderSide.BUY else reference - price


def _live_fee_rate(live: OrderOutcome) -> Tuple[Optional[float], str]:
    """
    The live order's fee rate, where it is the venue's own figure.

    Args:
        live: The live order

    Returns:
        (the rate in percent, '') — or (None, why it is not checked)
    """
    if live.filled_lots <= 0 or live.fill_price is None:
        return None, 'nothing was executed'
    if live.fee_source is not FeeSource.VENUE or live.fee is None:
        return None, "the fee is not the venue's own figure"
    return live.fee / (live.filled_lots * live.fill_price) * 100.0, ''


def _fee_source_of(fills: Sequence[OrderEventRow]) -> Optional[FeeSource]:
    """
    Whether an order's fees are the venue's own figures — only where every fill's is.

    Args:
        fills: The order's fills

    Returns:
        VENUE where every fill's fee is the venue's; ESTIMATE where one is not; None without fills
    """
    if not fills:
        return None
    return (FeeSource.VENUE if all(event.fee_source is FeeSource.VENUE for event in fills)
            else FeeSource.ESTIMATE)


def _refusal_text(event: OrderEventRow) -> str:
    """
    A refusal in a few words: its reason and the venue's code.

    Args:
        event: The refusal event

    Returns:
        The text
    """
    reason = event.rejection_reason.value if event.rejection_reason is not None else 'refused'
    return f'{reason} ({event.venue_reason})' if event.venue_reason else reason


def _instant(text: Optional[str]) -> Optional[datetime]:
    """
    An event time read back.

    Args:
        text: The ISO text; None where the event carries none

    Returns:
        The instant; None as above
    """
    return parse_iso_datetime(text) if text else None
