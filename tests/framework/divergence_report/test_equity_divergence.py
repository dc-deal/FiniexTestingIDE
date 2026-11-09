"""
Measure 3 of the divergence report (#512) — how far the two runs' account values ran apart.

Frames on both sides with known values: the shared cadence slots and their gaps, each compared
day's change on both sides, and the days' differences read as a series.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import pytest

from python.framework.reporting.builders.divergence.equity_divergence_builder import (
    track_equity,
    valued_frame_before,
)
from python.framework.types.api.divergence_report_types import TradingDayClassification
from python.framework.types.api.state_frame_types import StateFrame, StateFrameCause
from python.framework.utils.paired_difference_statistics import ninety_percent_interval

_FIRST = date(2026, 11, 25)
_START = datetime(2026, 11, 25, tzinfo=timezone.utc)


def _frame(at: datetime, equity: Optional[float], slot: bool = True) -> StateFrame:
    return StateFrame(
        frame_time=at, written_at=at, written_for=StateFrameCause.CADENCE,
        cadence_slot=at.replace(second=0, microsecond=0) if slot else None, ticks_processed=1,
        symbol='ETHUSD', account_currency='USD', last_price=3000.0, balance=equity or 0.0,
        equity=equity, open_positions=[])


def _day(offset: int, included: bool = True) -> TradingDayClassification:
    opening = _START + timedelta(days=offset)
    return TradingDayClassification(
        trading_day=_FIRST + timedelta(days=offset), opening=opening,
        closing=opening + timedelta(days=1), run_id='s1', included=included)


def _frames(values: Dict[int, float]) -> List[StateFrame]:
    """One frame a minute before each day's end and one just after its start, by day offset."""
    frames = [_frame(_START - timedelta(minutes=1), 1000.0)]
    for offset, value in sorted(values.items()):
        frames.append(_frame(_START + timedelta(days=offset, minutes=1), value))
        frames.append(_frame(_START + timedelta(days=offset + 1, minutes=-1), value))
    return frames


class TestTheSlots:

    def test_only_slots_both_sides_valued_inside_a_compared_day(self):
        live = _frames({0: 1010.0, 1: 1030.0})
        backtest = _frames({0: 1000.0})
        tracking = track_equity([_day(0), _day(1)], live, backtest, {}, {})
        assert tracking.shared_slots == 2
        assert tracking.largest_gap.gap == pytest.approx(10.0)
        assert tracking.gap_at_end.at == _START + timedelta(days=1, minutes=-1)

    def test_an_unvalued_frame_is_no_slot(self):
        live = [_frame(_START + timedelta(minutes=5), None)]
        backtest = [_frame(_START + timedelta(minutes=5), 1000.0)]
        assert track_equity([_day(0)], live, backtest, {}, {}).shared_slots == 0


class TestTheDays:

    def test_each_side_changes_in_percent_of_its_opening_value(self):
        live = _frames({0: 1010.0})
        backtest = _frames({0: 1005.0})
        day, = track_equity([_day(0)], live, backtest, {}, {}).days
        assert day.live_change_pct == pytest.approx(1.0)
        assert day.backtest_change_pct == pytest.approx(0.5)
        assert day.difference_pct == pytest.approx(0.5)
        assert day.closing_gap == pytest.approx(5.0)

    def test_an_excluded_day_is_not_measured(self):
        tracking = track_equity([_day(0, included=False)], _frames({0: 1010.0}),
                                _frames({0: 1010.0}), {}, {})
        assert tracking.days == [] and tracking.daily.day_count == 0

    def test_a_side_without_a_frame_before_the_opening_has_no_change(self):
        live = _frames({0: 1010.0})[1:]
        day, = track_equity([_day(0)], live, _frames({0: 1000.0}), {}, {}).days
        assert day.live_opening_equity is None and day.difference_pct is None

    def test_the_days_differences_read_as_a_series(self):
        live = _frames({0: 1010.0, 1: 1030.2, 2: 1030.2})
        backtest = _frames({0: 1000.0, 1: 1010.0, 2: 1010.0})
        tracking = track_equity([_day(0), _day(1), _day(2)], live, backtest, {}, {})
        differences = [day.difference_pct for day in tracking.days]
        assert tracking.daily.day_count == 3
        assert tracking.daily.mean_pct == pytest.approx(sum(differences) / 3)
        assert (tracking.daily.interval_low_pct, tracking.daily.interval_high_pct) == \
            pytest.approx(ninety_percent_interval(differences))
        assert tracking.daily.interval_too_narrow is not None


def test_the_valued_frame_before_skips_unvalued_ones_and_never_takes_the_instant_itself():
    frames = [_frame(_START, 1000.0), _frame(_START + timedelta(minutes=1), None),
              _frame(_START + timedelta(minutes=2), 1001.0)]
    assert valued_frame_before(frames, _START + timedelta(minutes=2)).equity == 1000.0
    assert valued_frame_before(frames, _START) is None
