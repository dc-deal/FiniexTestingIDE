"""
Measure 1 of the divergence report (#512) — each compared day's decision on both sides.

A day's decision is the frame of the pass in which the decision bar closed; on a segment's first
day, the pass of its first tick. The two days agree where the action and the number of orders are
the same, and are one-sided otherwise. A one-sided day is proposed as one of the four explained
kinds where one fits, with the figures that show it — a proposal for a person to check: whether it
holds, and whether the measure passes, is the verdict's. Pure: it reads the frames it is handed.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from python.framework.types.api.divergence_report_types import (
    DayDecision,
    DayOrderDivergence,
    DecisionComparison,
    DecisionComparisonState,
    DecisionTable,
    DecisionTableCell,
    ExplainedFlipKind,
    TradingDayClassification,
)
from python.framework.types.api.state_frame_types import StateFrame
from python.framework.types.decision_logic_types import DecisionLogicAction

# How many of a day's differing worker outputs a detail names
_NAMED_OUTPUTS = 3


def compare_decisions(days: Sequence[TradingDayClassification], segment_start: Optional[datetime],
                      live_frames: Sequence[StateFrame], backtest_frames: Sequence[StateFrame],
                      orders: Mapping[datetime, DayOrderDivergence], decision_timeframe: str,
                      restarts: Mapping[str, Tuple[datetime, Optional[timedelta]]]
                      ) -> Tuple[List[DecisionComparison], DecisionTable]:
    """
    Every compared day's decision on both sides, and the days counted by their two actions.

    Args:
        days: The segment's days; only the compared ones are measured
        segment_start: The segment's first tick — where its first day's decision is taken
        live_frames: The live sessions' frames, in time order
        backtest_frames: The backtest scenario's frames, in time order
        orders: Each compared day's measured orders, by its closing
        decision_timeframe: The timeframe whose bar's close is the day's decision
        restarts: Each session that started after the segment's first, by its run id: its start
            and how far back its warmup reached

    Returns:
        (one comparison per compared day, in day order; the table of their actions)
    """
    comparisons = [compare_day(day, segment_start if day.first_of_segment else None,
                               live_frames, backtest_frames, orders.get(day.closing),
                               decision_timeframe, restarts)
                   for day in days if day.included]
    return comparisons, decision_table(comparisons)


def compare_day(day: TradingDayClassification, first_tick: Optional[datetime],
                live_frames: Sequence[StateFrame], backtest_frames: Sequence[StateFrame],
                orders: Optional[DayOrderDivergence], decision_timeframe: str,
                restarts: Mapping[str, Tuple[datetime, Optional[timedelta]]]
                ) -> DecisionComparison:
    """
    One compared day's decision on both sides.

    Args:
        day: The day
        first_tick: The segment's first tick where this is its first day; None otherwise
        live_frames: The live frames, in time order
        backtest_frames: The backtest's frames, in time order
        orders: The day's measured orders; None where they were not measured
        decision_timeframe: The timeframe whose bar's close is the decision
        restarts: The sessions that started after the segment's first, as above

    Returns:
        Whether they agree, and for a one-sided day the explained kind it is proposed as
    """
    live_frame = decision_frame_on(live_frames, day, first_tick, decision_timeframe)
    backtest_frame = decision_frame_on(backtest_frames, day, first_tick, decision_timeframe)
    live_orders = (len(orders.orders) + len(orders.live_one_sided)) if orders else 0
    backtest_orders = (len(orders.orders) + len(orders.backtest_one_sided)) if orders else 0
    if live_frame is None or backtest_frame is None:
        missing = ' and '.join(side for side, frame in (('live', live_frame),
                                                        ('backtest', backtest_frame))
                               if frame is None)
        return DecisionComparison(
            trading_day=day.trading_day, closing=day.closing,
            state=DecisionComparisonState.NOT_CHECKED,
            live=_decision(live_frame, live_orders), backtest=_decision(backtest_frame,
                                                                       backtest_orders),
            detail=f'no {decision_timeframe} decision frame on the {missing} side')
    live = _decision(live_frame, live_orders)
    backtest = _decision(backtest_frame, backtest_orders)
    if live.action == backtest.action and live.order_count == backtest.order_count:
        return DecisionComparison(trading_day=day.trading_day, closing=day.closing,
                                  state=DecisionComparisonState.AGREE, live=live,
                                  backtest=backtest)
    kind, detail = propose_kind(day, live, backtest, restarts)
    return DecisionComparison(
        trading_day=day.trading_day, closing=day.closing, state=DecisionComparisonState.ONE_SIDED,
        live=live, backtest=backtest, proposed_kind=kind, detail=detail)


def decision_frame_on(frames: Sequence[StateFrame], day: TradingDayClassification,
                      first_tick: Optional[datetime],
                      decision_timeframe: str) -> Optional[StateFrame]:
    """
    One run's decision frame of one day.

    Args:
        frames: The run's frames, in time order
        day: The day
        first_tick: The segment's first tick on its first day; None otherwise
        decision_timeframe: The timeframe whose bar's close is the decision

    Returns:
        The first frame from the first tick on, on the segment's first day; on any other day the
        first frame inside it whose pass closed a bar of the decision timeframe; None where there is
        none
    """
    if first_tick is not None:
        return next((frame for frame in frames
                     if first_tick <= frame.frame_time < day.closing), None)
    return next((frame for frame in frames
                 if day.opening <= frame.frame_time < day.closing
                 and decision_timeframe in frame.closed_bars), None)


def propose_kind(day: TradingDayClassification, live: DayDecision, backtest: DayDecision,
                 restarts: Mapping[str, Tuple[datetime, Optional[timedelta]]]
                 ) -> Tuple[Optional[ExplainedFlipKind], str]:
    """
    The explained kind a one-sided day is proposed as, with the figures that show it.

    Args:
        day: The day
        live: The session's decision
        backtest: The backtest's decision
        restarts: The sessions that started after the segment's first, as above

    Returns:
        (the kind, or None where none fits; one line naming the day, the time and the figures)
    """
    head = (f'{day.trading_day.isoformat()} {live.frame_time:%H:%M} · live {_action(live)} with '
            f'{live.order_count} order(s) · backtest {_action(backtest)} with '
            f'{backtest.order_count} order(s)')
    if live.safety_blocked and live.order_count < backtest.order_count:
        return ExplainedFlipKind.LIVE_BREAKER, f'{head} · the live circuit breaker was holding'
    differing = _differing_outputs(live.worker_outputs, backtest.worker_outputs)
    if differing:
        named = '; '.join(differing[:_NAMED_OUTPUTS])
        restart = _restart_in_reach(day, restarts)
        if restart is not None:
            return ExplainedFlipKind.REWARMUP, (
                f'{head} · {restart} · workers differ: {named}')
        return ExplainedFlipKind.CAPTURE, (
            f"{head} · workers differ: {named} · unconfirmed until the venue's trades show it")
    if live.equity is not None and backtest.equity is not None and live.equity != backtest.equity:
        return ExplainedFlipKind.ACCOUNT_VALUE, (
            f'{head} · the workers agree, the account values differ: {live.equity:.2f} live, '
            f'{backtest.equity:.2f} backtest')
    return None, f'{head} · the workers and the account values agree — unexplained'


def decision_table(comparisons: Sequence[DecisionComparison]) -> DecisionTable:
    """
    The compared days counted by their two actions.

    Args:
        comparisons: The days' comparisons

    Returns:
        One cell per pair of actions that occurred, and the days not checked
    """
    counts: Dict[Tuple[DecisionLogicAction, DecisionLogicAction], int] = {}
    not_checked = 0
    for comparison in comparisons:
        live = comparison.live.action if comparison.live is not None else None
        backtest = comparison.backtest.action if comparison.backtest is not None else None
        if (comparison.state is DecisionComparisonState.NOT_CHECKED or live is None
                or backtest is None):
            not_checked += 1
            continue
        counts[(live, backtest)] = counts.get((live, backtest), 0) + 1
    order = list(DecisionLogicAction)
    return DecisionTable(
        cells=[DecisionTableCell(live_action=live, backtest_action=backtest, days=days)
               for (live, backtest), days in sorted(
                   counts.items(), key=lambda item: (order.index(item[0][0]),
                                                     order.index(item[0][1])))],
        not_checked=not_checked)


def _decision(frame: Optional[StateFrame], order_count: int) -> Optional[DayDecision]:
    """
    A run's decision as its frame recorded it.

    Args:
        frame: The decision frame; None where none was found
        order_count: How many orders the run submitted that day

    Returns:
        The decision; None without a frame
    """
    if frame is None:
        return None
    return DayDecision(frame_time=frame.frame_time, action=frame.decision_action,
                       order_count=order_count, equity=frame.equity,
                       safety_blocked=frame.safety_blocked, worker_outputs=frame.worker_outputs,
                       decision_outputs=frame.decision_outputs)


def _differing_outputs(live: Mapping[str, Mapping[str, object]],
                       backtest: Mapping[str, Mapping[str, object]]) -> List[str]:
    """
    Every worker output the two runs read differently, in a few words each.

    Args:
        live: The session's worker outputs
        backtest: The backtest's

    Returns:
        `worker.key live / backtest` per differing output, in worker and key order
    """
    differing: List[str] = []
    for worker in sorted(set(live) | set(backtest)):
        ours, theirs = live.get(worker, {}), backtest.get(worker, {})
        for key in sorted(set(ours) | set(theirs)):
            if ours.get(key) != theirs.get(key):
                differing.append(f'{worker}.{key} {ours.get(key)} / {theirs.get(key)}')
    return differing


def _restart_in_reach(day: TradingDayClassification,
                      restarts: Mapping[str, Tuple[datetime, Optional[timedelta]]]
                      ) -> Optional[str]:
    """
    The restart whose warmup still reaches into a day's decision, if any.

    Args:
        day: The day
        restarts: The sessions that started after the segment's first, as above

    Returns:
        The restart in a few words; None where none reaches the day
    """
    for run_id, (started, reach) in sorted(restarts.items(), key=lambda item: item[1][0]):
        if reach is not None and started <= day.opening <= started + reach:
            return f'{run_id} restarted {started:%d %b %H:%M}, its warmup reaching {reach}'
    return None


def _action(decision: DayDecision) -> str:
    """
    A decision's action as a word.

    Args:
        decision: The decision

    Returns:
        BUY, SELL or FLAT; 'none' where the frame names no decision
    """
    return decision.action.value if decision.action is not None else 'none'

