"""
The verdict of the divergence report (#512) — each measure of a segment held to the bound the
verdict rules fixed before the run, and the segment's verdict from them.

Every measure ends passed, failed or not checked, with its reason; a missing record is never a
pass. Beside the verdict, as a display that decides nothing: each category's share of deviating
items, the weighted cross-section over the binding categories, the longest runs of deviating and
of excluded days, and how many chances each measure had. Measure 3 is reported, never judged, in
this run; in the rehearsal's reading measure 2 is reported too, and no floor of days applies.

Pure: it reads the live side's segment and the segment's measures.
"""

from datetime import date, timedelta
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

from python.framework.constants.divergence_verdict_constants import (
    ATTRIBUTION_RESIDUAL_BOUND,
    CROSS_SECTION_WEIGHTS,
    EXECUTION_PRICE_BOUND_PCT,
    EXPLAINED_FLIPS_MAX,
    FEE_RATE_BOUND_PP,
    MINIMUM_COMPARED_DAYS,
    RUN_TRADING_DAYS,
    SIZE_BOUND_PCT,
    YELLOW_ZONE_MAX_ORDERS,
)
from python.framework.types.api.divergence_report_types import (
    CrossSection,
    DayExclusionReason,
    DayOrderDivergence,
    DecisionComparison,
    DecisionComparisonState,
    DeviatingItem,
    DivergenceMeasure,
    DivergenceReading,
    ExplainedFlipKind,
    MeasureChances,
    MeasureState,
    MeasureVerdict,
    OrderDivergence,
    SegmentMeasures,
    SegmentReport,
    SegmentVerdict,
    TradingDayClassification,
    Zone,
)

# The measures the verdict binds in each reading; the others are reported
_BINDING = {
    DivergenceReading.RUN: (DivergenceMeasure.DECISIONS, DivergenceMeasure.FILL_COMPLETENESS,
                            DivergenceMeasure.EXECUTION_PRICE, DivergenceMeasure.FEE_RATE,
                            DivergenceMeasure.SIZE, DivergenceMeasure.ATTRIBUTION),
    DivergenceReading.REHEARSAL: (DivergenceMeasure.DECISIONS, DivergenceMeasure.ATTRIBUTION),
}

# One question a measure-2 check asks of a paired order, answered in words or None
_OrderCheck = Callable[[OrderDivergence], Optional[str]]


def judge_segment(measures: SegmentMeasures, reading: DivergenceReading,
                  lot_step: Optional[float]) -> SegmentVerdict:
    """
    A segment's verdict from its measures.

    Args:
        measures: The segment's days and measures
        reading: The rules it is read by
        lot_step: The symbol's lot step, which bounds measure 2a; None where it is unknown

    Returns:
        Each measure's verdict, the cross-section, the clustering and the segment's state
    """
    compared = [day for day in measures.days if day.included]
    excluded = [day for day in measures.days if not day.included
                and DayExclusionReason.AFTER_WINDOW_END not in day.exclusions]
    chances = _chances(compared, measures.orders)
    order_days = [day for day in measures.orders if any(d.trading_day == day.trading_day
                                                        for d in compared)]
    verdicts = [
        judge_decisions(measures.decisions, chances),
        _judge_orders(DivergenceMeasure.FILL_COMPLETENESS, order_days,
                      _refusal_or_lot_step(lot_step), _lots_beyond(lot_step), chances,
                      bound=f'filled lots equal within one lot step ({lot_step})', zones=False),
        _judge_orders(DivergenceMeasure.EXECUTION_PRICE, order_days, _execution_comparable,
                      _execution_beyond, chances,
                      bound=f'at most {EXECUTION_PRICE_BOUND_PCT} % of the submission mid'),
        _judge_orders(DivergenceMeasure.FEE_RATE, order_days, _fee_comparable, _fee_beyond,
                      chances,
                      bound=f'within {FEE_RATE_BOUND_PP} percentage points of the taker rate'),
        _judge_orders(DivergenceMeasure.SIZE, order_days, _size_comparable, _size_beyond, chances,
                      bound=f'lots ratio within {SIZE_BOUND_PCT} % of the equity ratio'),
        judge_attribution(measures, compared, chances),
        _curve_tracking(measures, chances),
    ]
    binding = _BINDING[reading]
    floor = MINIMUM_COMPARED_DAYS if reading is DivergenceReading.RUN else None
    verdicts = [_applied(verdict, verdict.measure in binding, floor, len(compared))
                for verdict in verdicts]
    judged = [verdict for verdict in verdicts if verdict.measure in binding]
    state, reason = _segment_state(judged)
    return SegmentVerdict(
        state=state, reason=reason, reading=reading, compared_days=len(compared),
        excluded_days=len(excluded), day_floor=floor,
        longest_excluded_run=_longest_run({day.trading_day for day in excluded}),
        measures=verdicts, cross_section=cross_section(verdicts),
        notes=_notes(measures, reading))


def judge_decisions(decisions: Sequence[DecisionComparison],
                    chances: MeasureChances) -> MeasureVerdict:
    """
    Measure 1: no one-sided decision but named explained ones, at most two in all.

    Args:
        decisions: Each compared day's decision on both sides
        chances: The segment's chances

    Returns:
        The verdict, every one-sided day named
    """
    judged = [c for c in decisions if c.state in (DecisionComparisonState.AGREE,
                                                  DecisionComparisonState.ONE_SIDED)]
    one_sided = [c for c in judged if c.state is DecisionComparisonState.ONE_SIDED]
    unchecked = [c for c in decisions if c.state is DecisionComparisonState.NOT_CHECKED]
    in_gap = [c for c in decisions if c.state is DecisionComparisonState.IN_GAP]
    items = [DeviatingItem(trading_day=c.trading_day,
                           at=c.live.frame_time if c.live is not None else None,
                           detail=c.detail, explained=_explained(c)) for c in one_sided]
    explained = sum(1 for item in items if item.explained)
    unexplained = len(items) - explained
    bound = f'no one-sided decision but named explained ones, at most {EXPLAINED_FLIPS_MAX}'
    if unexplained or explained > EXPLAINED_FLIPS_MAX:
        state, reason = MeasureState.FAILED, (
            f'{len(items)} of {len(judged)} days one-sided, {explained} explained'
            + (f', {unexplained} not' if unexplained else f' — more than {EXPLAINED_FLIPS_MAX}'))
    elif unchecked:
        state, reason = MeasureState.NOT_CHECKED, (
            f'{len(unchecked)} day(s) without a decision frame on one side: '
            + ', '.join(c.trading_day.isoformat() for c in unchecked))
    elif not judged:
        state, reason = MeasureState.NOT_CHECKED, 'no compared day with a decision'
    else:
        state, reason = MeasureState.PASSED, (
            f'{len(items)} of {len(judged)} days one-sided, {explained} explained')
    if in_gap:
        reason += f'; {len(in_gap)} day(s) left out by a gap'
    return MeasureVerdict(
        measure=DivergenceMeasure.DECISIONS, state=state, reason=reason, bound=bound,
        compared=len(judged), deviating=len(items), share=_share(len(items), len(judged)),
        items=items, longest_deviating_run=_longest_run(item.trading_day for item in items),
        chances=chances)


def judge_attribution(measures: SegmentMeasures, compared: Sequence[TradingDayClassification],
                      chances: MeasureChances) -> MeasureVerdict:
    """
    The day attribution's residual — the report's own validity: each day's difference taken apart
    without more than the bound left over.

    Args:
        measures: The segment's measures
        compared: The compared days
        chances: The segment's chances

    Returns:
        The verdict; a day beyond the bound, or not taken apart, is named
    """
    compared_days = {day.trading_day for day in compared}
    days = [day for day in measures.attribution if day.trading_day in compared_days]
    items: List[DeviatingItem] = []
    computed = 0
    for day in days:
        if day.difference is None:
            items.append(DeviatingItem(trading_day=day.trading_day, at=day.closing,
                                       detail=f'not taken apart: {day.not_computed_because}'))
            continue
        computed += 1
        if abs(day.difference.residual) > ATTRIBUTION_RESIDUAL_BOUND:
            items.append(DeviatingItem(
                trading_day=day.trading_day, at=day.closing,
                detail=f'residual {day.difference.residual:+.4f} beyond '
                       f'{ATTRIBUTION_RESIDUAL_BOUND} — a size the equation lacks'))
    if items:
        state = MeasureState.NOT_CHECKED
        reason = f'{len(items)} of {len(days)} day(s) not taken apart'
    elif not days:
        state, reason = MeasureState.NOT_CHECKED, 'no compared day to take apart'
    else:
        state, reason = MeasureState.PASSED, f'{computed} day(s) taken apart within the bound'
    return MeasureVerdict(
        measure=DivergenceMeasure.ATTRIBUTION, state=state, reason=reason,
        bound=f'a residual of at most {ATTRIBUTION_RESIDUAL_BOUND} per day',
        compared=len(days), deviating=len(items), share=_share(len(items), len(days)),
        items=items, longest_deviating_run=_longest_run(item.trading_day for item in items),
        chances=chances)


def cross_section(verdicts: Sequence[MeasureVerdict]) -> CrossSection:
    """
    The weighted deviation over the binding categories.

    Args:
        verdicts: The measures' verdicts

    Returns:
        Σ weight × share and its agreement; not checked, naming the categories, where one had
        nothing to compare
    """
    by_name = {verdict.measure.value: verdict for verdict in verdicts}
    missing = [DivergenceMeasure(name) for name in CROSS_SECTION_WEIGHTS
               if by_name.get(name) is None or by_name[name].share is None]
    if missing:
        return CrossSection(weights=dict(CROSS_SECTION_WEIGHTS), not_checked=missing)
    deviation = sum(weight * by_name[name].share for name, weight in CROSS_SECTION_WEIGHTS.items())
    return CrossSection(weights=dict(CROSS_SECTION_WEIGHTS), weighted_deviation=deviation,
                        agreement=1.0 - deviation)


def _judge_orders(measure: DivergenceMeasure, days: Sequence[DayOrderDivergence],
                  comparable: _OrderCheck, deviation: _OrderCheck, chances: MeasureChances,
                  bound: str, zones: bool = True) -> MeasureVerdict:
    """
    One per-order measure of measure 2 over the compared days' paired orders.

    Args:
        measure: The measure
        days: The compared days' orders
        comparable: Whether an order is compared — None where it is, '' where it is left out
            silently (not filled on both sides), else why it could not be checked
        deviation: An order's deviation in words; None within the bound
        chances: The segment's chances
        bound: The bound in words
        zones: Whether the measure ends in zones (2b, 2c, 2d) or passes only without a deviation

    Returns:
        The verdict, every deviating order named with its delays
    """
    compared = 0
    unchecked: List[str] = []
    items: List[DeviatingItem] = []
    for day in days:
        for order in day.orders:
            why_not = comparable(order)
            if why_not is not None:
                if why_not:
                    unchecked.append(why_not)
                continue
            compared += 1
            found = deviation(order)
            if found is not None:
                items.append(DeviatingItem(trading_day=day.trading_day,
                                           at=order.live.submitted_at,
                                           detail=f'{found}{_delays(order)}'))
    zone = None
    if zones and compared:
        zone = (Zone.GREEN if not items else Zone.YELLOW if len(items) <= YELLOW_ZONE_MAX_ORDERS
                else Zone.RED)
    if items and (not zones or zone is Zone.RED):
        state, reason = MeasureState.FAILED, f'{len(items)} of {compared} order(s) beyond the bound'
    elif not compared:
        state, reason = MeasureState.NOT_CHECKED, (
            f'no order checkable: {unchecked[0]}' if unchecked else 'no order to compare')
    elif items:
        state, reason = MeasureState.PASSED, (
            f'{len(items)} of {compared} order(s) beyond the bound — yellow: each one is listed '
            f'with its delays and passes once traced to its cause')
    else:
        state, reason = MeasureState.PASSED, f'{compared} order(s) within the bound'
    if unchecked:
        reason += f'; {len(unchecked)} order(s) not checked'
    return MeasureVerdict(
        measure=measure, state=state, reason=reason, bound=bound, zone=zone, compared=compared,
        deviating=len(items), share=_share(len(items), compared), items=items,
        longest_deviating_run=_longest_run(item.trading_day for item in items), chances=chances)


def _refusal_or_lot_step(lot_step: Optional[float]) -> _OrderCheck:
    """
    Which orders measure 2a compares: every paired order but one a recorded refusal explains.

    Args:
        lot_step: The symbol's lot step; None where it is unknown

    Returns:
        The check
    """
    def comparable(order: OrderDivergence) -> Optional[str]:
        if order.live.refusal or order.backtest.refusal:
            return ''
        return "the symbol's lot step is unknown" if lot_step is None else None
    return comparable


def _lots_beyond(lot_step: Optional[float]) -> _OrderCheck:
    """
    Measure 2a's deviation: filled lots apart by more than one lot step.

    Args:
        lot_step: The symbol's lot step

    Returns:
        The check
    """
    def deviation(order: OrderDivergence) -> Optional[str]:
        if lot_step is not None and abs(order.lots_difference) > lot_step + 1e-12:
            return (f'filled {order.live.filled_lots} live, {order.backtest.filled_lots} in the '
                    f'backtest')
        return None
    return deviation


def _execution_comparable(order: OrderDivergence) -> Optional[str]:
    """
    Whether measure 2b compares an order: only one filled on both sides.

    Args:
        order: The paired order

    Returns:
        None where compared, '' where left out
    """
    return None if order.execution_difference_pct is not None else ''


def _execution_beyond(order: OrderDivergence) -> Optional[str]:
    """
    Measure 2b's deviation: the execution difference beyond its bound.

    Args:
        order: The paired order

    Returns:
        The deviation in words; None within the bound
    """
    if abs(order.execution_difference_pct) > EXECUTION_PRICE_BOUND_PCT:
        return f'execution {order.execution_difference_pct:+.4f} % of the submission mid'
    return None


def _fee_comparable(order: OrderDivergence) -> Optional[str]:
    """
    Whether measure 2c compares an order: only a venue fee against a known taker rate.

    Args:
        order: The paired order

    Returns:
        None where compared, '' where left out, else why it is not checked
    """
    if order.fee_not_checked_because:
        return order.fee_not_checked_because
    return None if order.live_fee_rate_pct is not None and order.taker_rate_pct is not None else ''


def _fee_beyond(order: OrderDivergence) -> Optional[str]:
    """
    Measure 2c's deviation: the venue's fee rate apart from the frozen taker rate.

    Args:
        order: The paired order

    Returns:
        The deviation in words; None within the bound
    """
    if abs(order.live_fee_rate_pct - order.taker_rate_pct) > FEE_RATE_BOUND_PP:
        return f'fee rate {order.live_fee_rate_pct:.4f} % against {order.taker_rate_pct:.4f} %'
    return None


def _size_comparable(order: OrderDivergence) -> Optional[str]:
    """
    Whether measure 2d compares an order: only with both ratios known.

    Args:
        order: The paired order

    Returns:
        None where compared, '' where left out
    """
    return None if order.lots_ratio is not None and order.equity_ratio else ''


def _size_beyond(order: OrderDivergence) -> Optional[str]:
    """
    Measure 2d's deviation: the lots ratio apart from the equity ratio.

    Args:
        order: The paired order

    Returns:
        The deviation in words; None within the bound
    """
    deviation_pct = abs(order.lots_ratio / order.equity_ratio - 1.0) * 100.0
    if deviation_pct > SIZE_BOUND_PCT:
        return (f'lots ratio {order.lots_ratio:.4f} against equity ratio '
                f'{order.equity_ratio:.4f} ({deviation_pct:.2f} %)')
    return None


def _delays(order: OrderDivergence) -> str:
    """
    The two delays from decision to submission, for tracing a deviation to its cause.

    Args:
        order: The paired order

    Returns:
        ' · delay live … s, backtest … s', or '' where neither is known
    """
    if order.live_delay is None and order.backtest_delay is None:
        return ''
    live = f'{order.live_delay:.2f} s' if order.live_delay is not None else '—'
    backtest = f'{order.backtest_delay:.2f} s' if order.backtest_delay is not None else '—'
    return f' · delay live {live}, backtest {backtest}'


def _curve_tracking(measures: SegmentMeasures, chances: MeasureChances) -> MeasureVerdict:
    """
    Measure 3, reported: the largest gap and the gap at the end.

    Args:
        measures: The segment's measures
        chances: The segment's chances

    Returns:
        The reported measure
    """
    tracking = measures.equity
    largest = tracking.largest_gap
    end = tracking.gap_at_end
    reason = (f'{tracking.shared_slots} shared slot(s), {tracking.slots_in_gaps} in gaps; '
              f'largest gap {largest.gap:+.4f} at {largest.at.isoformat()}, '
              f'at the end {end.gap:+.4f}' if largest is not None and end is not None
              else 'no shared slot')
    return MeasureVerdict(measure=DivergenceMeasure.CURVE_TRACKING, state=MeasureState.REPORTED,
                          reason=reason, bound='reported, never binding in this run',
                          compared=tracking.shared_slots, chances=chances)


def _applied(verdict: MeasureVerdict, binding: bool, floor: Optional[int],
             compared_days: int) -> MeasureVerdict:
    """
    A measure's verdict with the reading applied: a measure that does not bind is reported, and a
    pass over fewer days than the floor is not one.

    Args:
        verdict: The measure's verdict as judged
        binding: Whether the reading binds it
        floor: The fewest compared days a pass needs; None where none applies
        compared_days: The segment's compared days

    Returns:
        The verdict as the reading reads it
    """
    if not binding:
        return verdict.model_copy(update={'state': MeasureState.REPORTED})
    if floor is not None and verdict.state is MeasureState.PASSED and compared_days < floor:
        return verdict.model_copy(update={
            'state': MeasureState.NOT_CHECKED,
            'reason': f'{verdict.reason} — but over {compared_days} of {RUN_TRADING_DAYS} days, '
                      f'below the floor of {floor}'})
    return verdict


def _segment_state(judged: Sequence[MeasureVerdict]) -> Tuple[MeasureState, str]:
    """
    The segment's state from its binding measures.

    Args:
        judged: The binding measures' verdicts

    Returns:
        (failed where one failed, not checked where one was, passed otherwise; the sentence)
    """
    failed = [verdict.measure.value for verdict in judged if verdict.state is MeasureState.FAILED]
    unchecked = [verdict.measure.value for verdict in judged
                 if verdict.state is MeasureState.NOT_CHECKED]
    if failed:
        return MeasureState.FAILED, f'failed: {", ".join(failed)}'
    if unchecked:
        return MeasureState.NOT_CHECKED, f'not checked: {", ".join(unchecked)}'
    return MeasureState.PASSED, ('no divergence beyond the bounds over the compared days — '
                                 'never "parity proven"')


def _explained(comparison: DecisionComparison) -> bool:
    """
    Whether a one-sided day counts as explained: it names a kind, and a capture difference only
    once the venue's trades confirm it.

    Args:
        comparison: The one-sided day

    Returns:
        True where it counts as explained
    """
    if comparison.proposed_kind is None:
        return False
    if comparison.proposed_kind is ExplainedFlipKind.CAPTURE:
        return comparison.capture_confirmed is True
    return True


def _chances(compared: Sequence[TradingDayClassification],
             orders: Sequence[DayOrderDivergence]) -> MeasureChances:
    """
    How many chances the segment's measures had.

    Args:
        compared: The compared days
        orders: Each compared day's orders

    Returns:
        The compared days, the days with an order, the orders and the fills
    """
    compared_days = {day.trading_day for day in compared}
    days = [day for day in orders if day.trading_day in compared_days]
    with_order = [day for day in days
                  if day.orders or day.live_one_sided or day.backtest_one_sided]
    return MeasureChances(
        compared_days=len(compared),
        days_with_an_order=len(with_order),
        orders=sum(len(day.orders) + len(day.live_one_sided) + len(day.backtest_one_sided)
                   for day in days),
        fills=sum(day.live_fill_count + day.backtest_fill_count for day in days))


def _notes(measures: SegmentMeasures, reading: DivergenceReading) -> List[str]:
    """
    What a reader must know to read the verdict.

    Args:
        measures: The segment's measures
        reading: The rules it is read by

    Returns:
        The notes, in a sentence each
    """
    notes: List[str] = []
    first = next((c for c in measures.decisions
                  if c.live is not None and c.backtest is not None
                  and any(day.first_of_segment and day.trading_day == c.trading_day
                          for day in measures.days)), None)
    if first is not None and abs((first.live.frame_time
                                  - first.backtest.frame_time).total_seconds()) > 1.0:
        notes.append(
            f'{first.trading_day.isoformat()}: the session wrote no frame at its first tick — one '
            f'frame per minute slot, and its start took that slot — so its first decision is read '
            f'from the frame at {first.live.frame_time:%H:%M:%S}; only the action is compared')
    if reading is DivergenceReading.REHEARSAL:
        notes.append('read as the rehearsal: no floor of days, measure 2 described — a dry run '
                     'simulates its fills on both sides')
    return notes


def _share(deviating: int, compared: int) -> Optional[float]:
    """
    A deviating share.

    Args:
        deviating: k
        compared: N

    Returns:
        k ÷ N; None where nothing was compared
    """
    return deviating / compared if compared else None


def _longest_run(days: Iterable[date]) -> int:
    """
    The longest run of consecutive calendar days among some days.

    Args:
        days: The days, any order, repeats allowed

    Returns:
        The run's length; 0 for none
    """
    ordered: Set[date] = set(days)
    longest = 0
    for day in ordered:
        if day - timedelta(days=1) in ordered:
            continue
        length = 1
        while day + timedelta(days=length) in ordered:
            length += 1
        longest = max(longest, length)
    return longest


def exit_code_of(segments: Sequence[SegmentReport]) -> int:
    """
    The process exit code a comparison ends with.

    Args:
        segments: The comparison's segments

    Returns:
        0 where every segment was compared and passed; 2 where a segment was not compared — the
        archive, the backtest or the gate refused it; 3 where a compared segment failed or was not
        checked
    """
    if not segments or any(segment.not_compared_because for segment in segments):
        return 2
    if all(segment.verdict is not None and segment.verdict.state is MeasureState.PASSED
           for segment in segments):
        return 0
    return 3
