"""
The verdict of the divergence report (#512): each measure held to the bound the rules fixed, the
zones of the per-order measures, the floor of compared days, the cross-section that decides
nothing, the clustering, and the exit code a comparison ends with.
"""

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from python.framework.constants.divergence_verdict_constants import (
    CROSS_SECTION_WEIGHTS,
    MINIMUM_COMPARED_DAYS,
)
from python.framework.types.api.divergence_report_types import (
    AttributionParts,
    CompareWindow,
    DayAttribution,
    DayDecision,
    DayExclusionReason,
    DayOrderDivergence,
    DecisionComparison,
    DecisionComparisonState,
    DivergenceMeasure,
    DivergenceReading,
    ExplainedFlipKind,
    LiveSegment,
    MeasureState,
    OrderDivergence,
    OrderOutcome,
    SegmentMeasures,
    SegmentReport,
    SegmentVerdict,
    TradingDayClassification,
    WindowEndCause,
    Zone,
)
from python.framework.validators.divergence_verdict_validator import (
    exit_code_of,
    judge_segment,
)

_T0 = datetime(2026, 11, 24, tzinfo=timezone.utc)
_FIRST = date(2026, 11, 24)


def _day(offset: int, included: bool = True, first: bool = False,
         exclusions=()) -> TradingDayClassification:
    opening = _T0 + timedelta(days=offset)
    return TradingDayClassification(
        trading_day=_FIRST + timedelta(days=offset), opening=opening,
        closing=opening + timedelta(days=1), run_id='s1', included=included,
        first_of_segment=first, exclusions=list(exclusions))


def _decision(offset: int, state=DecisionComparisonState.AGREE,
              kind: Optional[ExplainedFlipKind] = None,
              confirmed: Optional[bool] = None) -> DecisionComparison:
    at = _T0 + timedelta(days=offset, seconds=1)
    return DecisionComparison(
        trading_day=_FIRST + timedelta(days=offset), closing=at + timedelta(days=1), state=state,
        live=DayDecision(frame_time=at), backtest=DayDecision(frame_time=at), proposed_kind=kind,
        capture_confirmed=confirmed, detail=f'day {offset}')


def _order(execution_pct: float = 0.01, fee_rate: float = 0.8, lots_ratio: float = 1.0,
           lots_difference: float = 0.0) -> OrderDivergence:
    at = _T0 + timedelta(hours=1)
    return OrderDivergence(
        live=OrderOutcome(run_id='s1', seq=1, submitted_at=at, filled_lots=0.05),
        backtest=OrderOutcome(run_id='b1', seq=1, submitted_at=at, filled_lots=0.05),
        lots_difference=lots_difference, execution_difference=0.1,
        execution_difference_pct=execution_pct, live_fee_rate_pct=fee_rate, taker_rate_pct=0.8,
        lots_ratio=lots_ratio, equity_ratio=1.0, live_delay=1.8, backtest_delay=0.05)


def _order_day(offset: int, orders: List[OrderDivergence]) -> DayOrderDivergence:
    return DayOrderDivergence(trading_day=_FIRST + timedelta(days=offset),
                              closing=_T0 + timedelta(days=offset + 1), orders=orders,
                              live_fill_count=len(orders), backtest_fill_count=len(orders))


def _attribution(offset: int, residual: float = 0.0) -> DayAttribution:
    parts = AttributionParts(equity_change=0.0, held=0.0, delay=0.0, execution=0.0,
                             end_price=0.0, one_sided=0.0, fees=0.0, valuation=0.0,
                             residual=residual)
    return DayAttribution(trading_day=_FIRST + timedelta(days=offset),
                          closing=_T0 + timedelta(days=offset + 1), difference=parts)


def _measures(days: int = 30, decisions=None, orders=None, attribution=None,
              excluded=()) -> SegmentMeasures:
    classified = [_day(n, first=n == 0) for n in range(days)]
    classified += [_day(days + n, included=False,
                        exclusions=[DayExclusionReason.SESSION_KILLED_IN_DAY])
                   for n in range(len(excluded))]
    return SegmentMeasures(
        segment_index=1, scenario='segment_1', days=classified,
        decisions=decisions if decisions is not None else [_decision(n) for n in range(days)],
        orders=orders if orders is not None else [_order_day(0, [_order()])],
        attribution=(attribution if attribution is not None
                     else [_attribution(n) for n in range(days)]))


def _verdict(measures: SegmentMeasures, measure: DivergenceMeasure,
             reading=DivergenceReading.RUN):
    verdict = judge_segment(measures, reading, lot_step=0.0001)
    return verdict, next(m for m in verdict.measures if m.measure is measure)


class TestMeasure1:

    def test_agreeing_days_pass(self):
        _, decisions = _verdict(_measures(), DivergenceMeasure.DECISIONS)
        assert decisions.state is MeasureState.PASSED and decisions.share == 0.0

    def test_two_explained_flips_pass_and_are_named(self):
        days = [_decision(n) for n in range(28)] + [
            _decision(28, DecisionComparisonState.ONE_SIDED, ExplainedFlipKind.ACCOUNT_VALUE),
            _decision(29, DecisionComparisonState.ONE_SIDED, ExplainedFlipKind.LIVE_BREAKER)]
        _, decisions = _verdict(_measures(decisions=days), DivergenceMeasure.DECISIONS)
        assert decisions.state is MeasureState.PASSED
        assert decisions.deviating == 2 and all(item.explained for item in decisions.items)
        assert decisions.longest_deviating_run == 2

    def test_a_third_explained_flip_fails(self):
        days = [_decision(n) for n in range(27)] + [
            _decision(n, DecisionComparisonState.ONE_SIDED, ExplainedFlipKind.ACCOUNT_VALUE)
            for n in (27, 28, 29)]
        _, decisions = _verdict(_measures(decisions=days), DivergenceMeasure.DECISIONS)
        assert decisions.state is MeasureState.FAILED

    def test_an_unexplained_flip_fails(self):
        days = [_decision(n) for n in range(29)] + [
            _decision(29, DecisionComparisonState.ONE_SIDED)]
        _, decisions = _verdict(_measures(decisions=days), DivergenceMeasure.DECISIONS)
        assert decisions.state is MeasureState.FAILED and '1 not' in decisions.reason

    def test_an_unconfirmed_capture_is_not_explained(self):
        days = [_decision(n) for n in range(29)] + [
            _decision(29, DecisionComparisonState.ONE_SIDED, ExplainedFlipKind.CAPTURE)]
        _, decisions = _verdict(_measures(decisions=days), DivergenceMeasure.DECISIONS)
        assert decisions.state is MeasureState.FAILED
        confirmed = days[:-1] + [_decision(29, DecisionComparisonState.ONE_SIDED,
                                           ExplainedFlipKind.CAPTURE, confirmed=True)]
        _, decisions = _verdict(_measures(decisions=confirmed), DivergenceMeasure.DECISIONS)
        assert decisions.state is MeasureState.PASSED

    def test_a_day_without_a_frame_is_never_a_pass(self):
        days = [_decision(n) for n in range(29)] + [
            _decision(29, DecisionComparisonState.NOT_CHECKED)]
        _, decisions = _verdict(_measures(decisions=days), DivergenceMeasure.DECISIONS)
        assert decisions.state is MeasureState.NOT_CHECKED


class TestTheZonesOfMeasure2:

    def _execution(self, beyond: int):
        orders = [_order()] * (5 - beyond) + [_order(execution_pct=0.25)] * beyond
        _, execution = _verdict(_measures(orders=[_order_day(0, orders)]),
                                DivergenceMeasure.EXECUTION_PRICE)
        return execution

    def test_green_yellow_and_red(self):
        assert self._execution(0).zone is Zone.GREEN
        yellow = self._execution(2)
        assert yellow.zone is Zone.YELLOW and yellow.state is MeasureState.PASSED
        assert all('delay live 1.80 s' in item.detail for item in yellow.items)
        red = self._execution(3)
        assert red.zone is Zone.RED and red.state is MeasureState.FAILED

    def test_a_fee_the_venue_did_not_charge_is_not_checked(self):
        order = _order().model_copy(update={'fee_not_checked_because': 'fee_source estimate'})
        _, fee = _verdict(_measures(orders=[_order_day(0, [order])]), DivergenceMeasure.FEE_RATE)
        assert fee.state is MeasureState.NOT_CHECKED and 'estimate' in fee.reason

    def test_fill_completeness_has_no_zone(self):
        _, lots = _verdict(_measures(orders=[_order_day(0, [_order(lots_difference=0.01)])]),
                           DivergenceMeasure.FILL_COMPLETENESS)
        assert lots.zone is None and lots.state is MeasureState.FAILED

    def test_size_beyond_its_bound(self):
        _, size = _verdict(_measures(orders=[_order_day(0, [_order(lots_ratio=1.05)])]),
                           DivergenceMeasure.SIZE)
        assert size.deviating == 1 and size.zone is Zone.YELLOW

    def test_no_order_is_not_checked_and_leaves_the_cross_section_out(self):
        verdict, execution = _verdict(_measures(orders=[]), DivergenceMeasure.EXECUTION_PRICE)
        assert execution.state is MeasureState.NOT_CHECKED and execution.share is None
        assert verdict.cross_section.agreement is None
        assert DivergenceMeasure.EXECUTION_PRICE in verdict.cross_section.not_checked


class TestTheSegment:

    def test_every_measure_within_its_bound_passes_and_the_cross_section_agrees(self):
        verdict, _ = _verdict(_measures(), DivergenceMeasure.DECISIONS)
        assert verdict.state is MeasureState.PASSED
        assert verdict.cross_section.weights == CROSS_SECTION_WEIGHTS
        assert verdict.cross_section.agreement == 1.0

    def test_below_the_floor_a_pass_is_not_one(self):
        verdict, decisions = _verdict(_measures(days=MINIMUM_COMPARED_DAYS - 1),
                                      DivergenceMeasure.DECISIONS)
        assert decisions.state is MeasureState.NOT_CHECKED and 'below the floor' in decisions.reason
        assert verdict.state is MeasureState.NOT_CHECKED

    def test_the_rehearsal_has_no_floor_and_describes_measure_2(self):
        verdict, execution = _verdict(_measures(days=5), DivergenceMeasure.EXECUTION_PRICE,
                                      DivergenceReading.REHEARSAL)
        assert verdict.day_floor is None and verdict.state is MeasureState.PASSED
        assert execution.state is MeasureState.REPORTED

    def test_a_residual_beyond_its_bound_is_a_defect_not_a_pass(self):
        attribution = [_attribution(n) for n in range(29)] + [_attribution(29, residual=0.5)]
        verdict, residual = _verdict(_measures(attribution=attribution),
                                     DivergenceMeasure.ATTRIBUTION)
        assert residual.state is MeasureState.NOT_CHECKED
        assert verdict.state is MeasureState.NOT_CHECKED

    def test_excluded_days_are_counted_and_their_run(self):
        verdict, _ = _verdict(_measures(excluded=(1, 2, 3)), DivergenceMeasure.DECISIONS)
        assert verdict.excluded_days == 3 and verdict.longest_excluded_run == 3

    def test_the_chances_beside_every_measure(self):
        _, decisions = _verdict(_measures(), DivergenceMeasure.DECISIONS)
        assert decisions.chances.compared_days == 30
        assert (decisions.chances.days_with_an_order, decisions.chances.orders,
                decisions.chances.fills) == (1, 1, 2)


def _segment_report(state: Optional[MeasureState], refused: str = '') -> SegmentReport:
    segment = LiveSegment(index=1, started_by=[], run_ids=['s1'],
                          window=CompareWindow(end_cause=WindowEndCause.UNTIL))
    verdict = (SegmentVerdict(state=state, reading=DivergenceReading.RUN)
               if state is not None else None)
    return SegmentReport(segment=segment, verdict=verdict, not_compared_because=refused)


class TestTheExitCode:

    def test_every_segment_passed(self):
        assert exit_code_of([_segment_report(MeasureState.PASSED)]) == 0

    def test_a_segment_not_compared(self):
        assert exit_code_of([_segment_report(MeasureState.PASSED),
                             _segment_report(None, 'the gate refuses')]) == 2
        assert exit_code_of([]) == 2

    def test_a_segment_failed_or_not_checked(self):
        assert exit_code_of([_segment_report(MeasureState.FAILED)]) == 3
        assert exit_code_of([_segment_report(MeasureState.NOT_CHECKED)]) == 3
