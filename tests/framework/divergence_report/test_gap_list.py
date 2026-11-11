"""
The gaps of a segment's window in the divergence report (#512), and what they leave out.

Each source on its own — the archive's bar gaps, runs of missing trade ids, the session's tick-
stream outages, pauses in its state frames — then merged into the stretches whose minutes leave
every measure on both sides: a decision whose boundary lies in one, an order submitted or filled
in one, a cadence slot in one.
"""

from datetime import date, datetime, timedelta, timezone

import numpy as np

from python.framework.reporting.builders.divergence.decision_divergence_builder import (
    compare_decisions,
)
from python.framework.reporting.builders.divergence.equity_divergence_builder import track_equity
from python.framework.reporting.builders.divergence.gap_list_builder import (
    archive_gaps_in,
    build_gap_list,
    frame_pauses,
    in_excluded,
    merged_spans,
    outage_gaps,
    trade_id_runs,
)
from python.framework.reporting.builders.divergence.order_divergence_builder import (
    leave_out_gaps,
)
from python.framework.types.api.divergence_report_types import (
    DayOrderDivergence,
    DecisionComparisonState,
    DivergenceGap,
    ExcludedSpan,
    GapSource,
    OrderDivergence,
    OrderOutcome,
    TradingDayClassification,
)
from python.framework.types.api.state_frame_types import StateFrame, StateFrameCause
from python.framework.types.autotrader_types.tick_stream_event_types import (
    TickStreamLossCause,
    TickStreamOutage,
)
from python.framework.types.coverage_report_types import Gap, GapCategory
from python.framework.types.decision_logic_types import DecisionLogicAction

_T0 = datetime(2026, 11, 25, tzinfo=timezone.utc)


def _at(minutes: float) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _frame(minutes: float, decision_bar: bool = False) -> StateFrame:
    at = _at(minutes)
    return StateFrame(
        frame_time=at, written_at=at, written_for=StateFrameCause.CADENCE,
        cadence_slot=at.replace(second=0, microsecond=0), ticks_processed=1, symbol='ETHUSD',
        account_currency='USD', last_price=3000.0, balance=1000.0, equity=1000.0,
        open_positions=[], decision_action=DecisionLogicAction.FLAT if decision_bar else None,
        closed_bars={'M5': at - timedelta(minutes=5)} if decision_bar else {})


def _outage(**changes) -> TickStreamOutage:
    values = dict(outage_no=1, symbol='ETHUSD', lost_at=_at(10), delivered_for_s=60.0,
                  detected_by=TickStreamLossCause.SILENCE, restored_at=_at(14),
                  last_public_trade_time=_at(9.9), first_public_trade_time=_at(14.1))
    values.update(changes)
    return TickStreamOutage(**values)


class TestEachSource:

    def test_an_archive_gap_across_the_windows_edge_counts(self):
        gaps = [Gap(gap_seconds=600, category=GapCategory.MODERATE, reason='',
                    gap_start=_at(-5).replace(tzinfo=None), gap_end=_at(5).replace(tzinfo=None)),
                Gap(gap_seconds=600, category=GapCategory.MODERATE, reason='',
                    gap_start=_at(100), gap_end=_at(110))]
        found = archive_gaps_in(gaps, _at(0), _at(60))
        assert [(gap.start, gap.end) for gap in found] == [(_at(-5), _at(5))]
        assert found[0].source is GapSource.ARCHIVE_BARS

    def test_a_run_of_missing_trade_ids_longer_than_the_bound(self):
        ids = np.array([5, 1, 2, 3, 10, 11])
        msc = np.array([int(_at(m).timestamp() * 1000) for m in (2.5, 0, 0.5, 1, 6, 6.5)])
        run, = trade_id_runs(ids, msc)
        assert (run.start, run.end) == (_at(2.5), _at(6)) and not run.confirmed
        assert (run.trade_id_before, run.trade_id_after) == (5, 10)
        assert 'trade ids 6–9 (4 missing)' in run.detail

    def test_a_short_run_is_no_gap(self):
        ids = np.array([1, 3])
        msc = np.array([int(_at(0).timestamp() * 1000), int(_at(2).timestamp() * 1000)])
        assert trade_id_runs(ids, msc) == []

    def test_an_outage_between_the_venues_trades_else_from_loss_to_restoration(self):
        between, = outage_gaps('s1', [_outage()])
        assert (between.start, between.end) == (_at(9.9), _at(14.1))
        noticed, = outage_gaps('s1', [_outage(last_public_trade_time=None)])
        assert (noticed.start, noticed.end) == (_at(10), _at(14))
        assert outage_gaps('s1', [_outage(last_public_trade_time=None, restored_at=None)]) == []

    def test_an_outage_reads_back_as_it_was_written(self):
        outage = _outage(attempts=3, outage_s=240.0, last_public_trade_id=7,
                         first_public_trade_id=12)
        record = {key: value for key, value in outage.to_record().items() if value is not None}
        assert TickStreamOutage.from_record(record) == outage

    def test_a_pause_in_the_frames_longer_than_the_bound(self):
        frames = [_frame(0), _frame(2), _frame(5.5), _frame(6)]
        pause, = frame_pauses('s1', frames)
        assert (pause.start, pause.end) == (_at(2), _at(5.5))


class TestTheList:

    def test_gaps_merge_into_stretches_and_an_unconfirmed_run_leaves_nothing_out(self):
        run = DivergenceGap(source=GapSource.TRADE_ID_RUN, start=_at(40), end=_at(45),
                            confirmed=False)
        gap_list = build_gap_list(
            _at(0), _at(60), [], [run], {'s1': [_outage()]},
            {'s1': [_frame(0), _frame(12), _frame(20)]})
        # two pauses in the frames (0 → 12 and 12 → 20 minutes) around the outage
        assert [gap.source for gap in gap_list.gaps] == [GapSource.FRAME_PAUSE,
                                                         GapSource.TICK_STREAM_OUTAGE,
                                                         GapSource.FRAME_PAUSE]
        assert gap_list.excluded == [ExcludedSpan(start=_at(0), end=_at(20))]
        assert gap_list.unconfirmed == [run]

    def test_a_run_the_venue_confirmed_leaves_its_minutes_out(self):
        run = DivergenceGap(source=GapSource.TRADE_ID_RUN, start=_at(40), end=_at(45),
                            confirmed=False, trade_id_before=100, trade_id_after=140)
        gap_list = build_gap_list(_at(0), _at(60), [], [run], {}, {},
                                  venue_answers={(100, 140): 31})
        assert gap_list.unconfirmed == [] and gap_list.refuted == []
        assert gap_list.excluded == [ExcludedSpan(start=_at(40), end=_at(45))]
        assert gap_list.gaps[0].venue_trades == 31

    def test_a_run_the_venue_published_no_trade_in_is_refuted(self):
        run = DivergenceGap(source=GapSource.TRADE_ID_RUN, start=_at(40), end=_at(45),
                            confirmed=False, trade_id_before=100, trade_id_after=140)
        gap_list = build_gap_list(_at(0), _at(60), [], [run], {}, {},
                                  venue_answers={(100, 140): 0})
        assert gap_list.gaps == [] and gap_list.excluded == [] and gap_list.unconfirmed == []
        refuted, = gap_list.refuted
        assert refuted.venue_trades == 0 and 'allocated without a trade' in refuted.detail

    def test_a_stretch_holds_its_start_and_not_its_end(self):
        spans = merged_spans([DivergenceGap(source=GapSource.FRAME_PAUSE, start=_at(10),
                                            end=_at(20))])
        assert in_excluded(_at(10), spans) and not in_excluded(_at(20), spans)
        assert not in_excluded(None, spans)


class TestWhatAGapLeavesOut:

    _SPANS = [ExcludedSpan(start=_at(9), end=_at(12))]

    def test_a_decision_whose_bar_closed_in_a_gap(self):
        day = TradingDayClassification(trading_day=date(2026, 11, 25), opening=_at(0),
                                       closing=_at(1440), run_id='s1', included=True)
        comparisons, table = compare_decisions(
            [day], None, [_frame(10, decision_bar=True)], [_frame(10, decision_bar=True)], {},
            'M5', {}, self._SPANS)
        assert comparisons[0].state is DecisionComparisonState.IN_GAP
        assert table.in_gap == 1 and table.cells == []

    def test_a_decision_whose_pass_came_late_after_its_bar_closed(self):
        day = TradingDayClassification(trading_day=date(2026, 11, 25), opening=_at(0),
                                       closing=_at(1440), run_id='s1', included=True)
        late = _frame(30, decision_bar=True).model_copy(
            update={'closed_bars': {'M5': _at(20)}})
        comparisons, _ = compare_decisions([day], None, [late], [_frame(30, decision_bar=True)],
                                           {}, 'M5', {}, [])
        assert comparisons[0].state is DecisionComparisonState.IN_GAP
        assert 'after its boundary' in comparisons[0].detail

    def test_an_order_submitted_or_filled_in_a_gap_is_named_and_left_out(self):
        inside = OrderOutcome(run_id='s1', seq=1, submitted_at=_at(10))
        outside = OrderOutcome(run_id='b1', seq=1, submitted_at=_at(30))
        late_fill = OrderOutcome(run_id='s1', seq=3, submitted_at=_at(8), filled_at=_at(11))
        day = DayOrderDivergence(
            trading_day=date(2026, 11, 25), closing=_at(1440),
            orders=[OrderDivergence(live=inside, backtest=outside, lots_difference=0.0)],
            live_one_sided=[late_fill])
        left = leave_out_gaps(day, self._SPANS)
        assert left.orders == [] and left.live_one_sided == []
        assert left.orders_in_gap == [inside, outside, late_fill]

    def test_a_slot_in_a_gap_is_left_out_of_measure_3(self):
        day = TradingDayClassification(trading_day=date(2026, 11, 25), opening=_at(0),
                                       closing=_at(1440), run_id='s1', included=True)
        frames = [_frame(5), _frame(10), _frame(15)]
        tracking = track_equity([day], frames, frames, {}, {}, self._SPANS)
        assert (tracking.shared_slots, tracking.slots_in_gaps) == (2, 1)
