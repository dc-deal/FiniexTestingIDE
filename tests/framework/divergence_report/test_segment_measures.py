"""
One segment's measures against a backtest of its window (#512): the resumption rule run with the
backtest's frames, every compared day paired with the backtest row that closed at the same
boundary, and measure 2, measure 3 and the day attribution of each.

The deployment is the one the live side's tests build — a session killed and restarted the same
day, so the days after the restart wait for both sides to hold the same position. The backtest
recorded the same orders and the same account values, so each measure shows nothing, and only the
position the backtest holds at a waiting day's opening decides whether that day is compared.
"""

import dataclasses
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from python.framework.reporting.builders.divergence.live_segment_builder import derive_segments
from python.framework.reporting.builders.divergence.live_trading_day_builder import (
    boundary_position_of,
    closing_of,
)
from python.framework.reporting.builders.divergence.segment_measures_builder import (
    measure_segment,
)
from python.framework.types.api.divergence_report_types import (
    DayExclusionReason,
    DecisionComparisonState,
)
from python.framework.types.api.report_types import OpenPositionRow
from python.framework.types.api.state_frame_types import StateFrame, StateFrameCause
from python.framework.types.decision_logic_types import DecisionLogicAction
from python.framework.types.divergence_capture_types import (
    BacktestRunCapture,
    BacktestScenarioRecords,
    RecordedFrames,
)
from python.framework.validators.divergence_day_validator import live_side_of
from tests.shared.divergence_live_chain import (
    CRYPTO_ANCHOR,
    FIRST_DAY,
    FIRST_TICK,
    SYMBOL,
    days_from,
    deployment,
    ledger_row,
    records_of,
    session,
)

_SCENARIO = 'my_recorded_window'
_R = DayExclusionReason


def _at(day_offset: int, hours: float) -> datetime:
    return FIRST_TICK.replace(minute=0) + timedelta(days=day_offset, hours=hours)


def _frame(at: datetime, eth: float = 0.0, decision_bar: bool = False) -> StateFrame:
    """A valued spot frame: 1000 USD and some ETH at a mid of 3000, a FLAT decision where a day bar
    closed in its pass."""
    return StateFrame(
        frame_time=at, written_at=at, written_for=StateFrameCause.CADENCE,
        closed_bars={'D1': at - timedelta(days=1)} if decision_bar else {},
        decision_action=DecisionLogicAction.FLAT if decision_bar else None,
        cadence_slot=at.replace(second=0, microsecond=0), ticks_processed=1, symbol=SYMBOL,
        account_currency='USD', last_price=3000.0, balance=1000.0,
        balances={'USD': 1000.0, 'ETH': eth}, equity=1000.0 + eth * 3000.0,
        open_positions=[OpenPositionRow(position_id='p1', direction='long', lots=eth,
                                        entry_price=3000.0, entry_time=at.isoformat())]
        if eth else [])


def _frames_over(first_day_offset: int, days: int,
                 eth_at: Optional[Dict[int, float]] = None) -> List[StateFrame]:
    """A frame half a minute before every opening of the days, and one at each day's noon."""
    frames = []
    for offset in range(first_day_offset, first_day_offset + days + 1):
        opening = closing_of(FIRST_DAY + timedelta(days=offset - 1), CRYPTO_ANCHOR)
        eth = (eth_at or {}).get(offset, 0.0)
        frames.append(_frame(opening - timedelta(seconds=30), eth))
        frames.append(_frame(opening + timedelta(hours=12), eth, decision_bar=True))
    return frames


def _measured(backtest_eth_at: Optional[Dict[int, float]] = None):
    killed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 3), ended_at=_at(3, 10))
    restarted = session('s2', _at(3, 11), days_from(FIRST_DAY + timedelta(days=3), 3),
                        first_period_no=4)
    capture = deployment(killed, restarted)
    segments, named = derive_segments(capture)
    live_frames = {'s1': RecordedFrames(_frames_over(0, 3)),
                   's2': RecordedFrames(_frames_over(3, 3))}
    # The position at each closing comes from the session's own frames, as the collector reads it
    day_records = {key: dataclasses.replace(records, closing_position=boundary_position_of(
        live_frames[key[0]].frames, closing_of(records.trading_day, CRYPTO_ANCHOR)))
        for key, records in {**records_of(killed), **records_of(restarted)}.items()}
    segment, = live_side_of(capture, segments, named, day_records).segments
    backtest_rows = [ledger_row('b1', period, FIRST_TICK, closing_of(day, CRYPTO_ANCHOR))
                     .model_copy(update={'unit_name': _SCENARIO})
                     for period, day in enumerate(days_from(FIRST_DAY, 6), start=1)]
    backtest_events = [event.model_copy(update={'scenario_name': _SCENARIO})
                       for records in day_records.values() for event in records.order_events]
    backtest = BacktestRunCapture(run_id='b1', app_version='1.5.0', code_identity=None,
                                  rows=backtest_rows, scenarios=[])
    records = BacktestScenarioRecords(
        scenario=_SCENARIO, order_events=backtest_events,
        frames=RecordedFrames(_frames_over(0, 6, backtest_eth_at)))
    return measure_segment(segment, capture, day_records, live_frames, backtest, records, 0.8,
                           'D1')


class TestTheSegmentAgainstABacktestOfIt:

    def test_the_days_after_the_restart_are_compared_where_both_sides_are_flat(self):
        measures = _measured()
        compared = [day.trading_day for day in measures.days if day.included]
        assert FIRST_DAY + timedelta(days=4) in compared
        assert FIRST_DAY + timedelta(days=5) in compared
        assert not any(_R.RESUMPTION_PENDING in day.exclusions for day in measures.days)

    def test_a_position_the_backtest_holds_keeps_a_waiting_day_out(self):
        measures = _measured(backtest_eth_at={4: 0.5})
        day5 = next(day for day in measures.days
                    if day.trading_day == FIRST_DAY + timedelta(days=4))
        assert _R.POSITIONS_APART in day5.exclusions

    def test_every_compared_day_is_paired_with_the_backtest_row_of_its_boundary(self):
        measures = _measured()
        compared = [day for day in measures.days if day.included]
        assert [paired.closing for paired in measures.paired_days] == [
            day.closing for day in compared]
        assert all(paired.backtest_row is not None for paired in measures.paired_days)

    def test_the_same_records_show_nothing_in_any_measure(self):
        measures = _measured()
        assert len(measures.orders) == len(measures.attribution) == len(measures.paired_days)
        for day in measures.orders:
            assert len(day.orders) == 1 and day.orders[0].lots_difference == 0
            assert not day.live_one_sided and not day.backtest_one_sided
        assert measures.equity.shared_slots > 0 and measures.equity.largest_gap.gap == 0.0
        assert len(measures.decisions) == len(measures.paired_days)
        assert all(day.state is DecisionComparisonState.AGREE for day in measures.decisions)
        cell, = measures.decision_table.cells
        assert (cell.live_action, cell.backtest_action) == (DecisionLogicAction.FLAT,) * 2
        for day in measures.attribution:
            assert day.not_computed_because == ''
            assert all(value == 0.0 for value in day.difference.model_dump().values())
        assert measures.unreadable == []

    def test_what_could_not_be_read_is_named(self):
        killed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2))
        capture = deployment(killed)
        segments, named = derive_segments(capture)
        day_records = records_of(killed)
        segment, = live_side_of(capture, segments, named, day_records).segments
        records = BacktestScenarioRecords(
            scenario=_SCENARIO, order_events=[],
            frames=RecordedFrames([], unreadable='the run records no state frames'))
        measures = measure_segment(
            segment, capture, day_records, {}, BacktestRunCapture(
                run_id='b1', app_version='1.5.0', code_identity=None, rows=[], scenarios=[]),
            records, None, None)
        assert any('s1: no state frames were read' in note for note in measures.unreadable)
        assert any('measure 1 was not computed' in note for note in measures.unreadable)
        assert any('backtest my_recorded_window state frames' in note
                   for note in measures.unreadable)
