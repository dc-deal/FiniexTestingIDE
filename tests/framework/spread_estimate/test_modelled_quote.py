"""
Modelled Quote Tests (#602).

A tick from a file without a quote is given the quote its day estimate implies before it leaves
for the scenario process. These tests hold the pure functions to the rules that make that honest:
a day is priced by the days before it and never by itself, a thin day is skipped, a gap longer
than the fallback leaves the tick as recorded, a factor of 0 changes nothing, and a tick that
recorded a quote is never touched.
"""

from datetime import date, datetime, timezone
from typing import List

import numpy as np
import pytest

from python.framework.data_preparation.modelled_quote import (
    FALLBACK_DAYS,
    MIN_PAIRS_PER_DAY,
    day_digest,
    median_spread_bps,
    modelled_quote,
    modelled_runs,
    usable_days,
)
from python.framework.types.spread_estimate_types import SpreadDayEstimate

DAY_MS = 86_400_000
# 2021-03-01 00:00:00 UTC
T0 = int(datetime(2021, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _day(offset: int, mean_bps: float, pairs: int = MIN_PAIRS_PER_DAY) -> SpreadDayEstimate:
    day = date.fromordinal(date(2021, 3, 1).toordinal() + offset)
    return SpreadDayEstimate(day=day, pairs=pairs, spread_median_bps=mean_bps,
                             spread_p90_bps=mean_bps, spread_mean_bps=mean_bps)


def _quote(days: List[SpreadDayEstimate], tick_days: List[int], factor: float = 1.0,
           without_quote: List[bool] = None):
    """Model one tick at noon of each given day offset, price 100."""
    time_msc = np.array([T0 + d * DAY_MS + DAY_MS // 2 for d in tick_days], dtype=np.int64)
    price = np.full(len(tick_days), 100.0)
    mask = np.array(without_quote if without_quote is not None else [True] * len(tick_days))
    usable, half = usable_days(days)
    return modelled_quote(time_msc, price, price.copy(), price.copy(), mask, usable, half, factor)


class TestADayIsPricedByTheDaysBeforeIt:

    def test_the_previous_day_prices_the_tick(self):
        bid, ask, modelled, _ = _quote([_day(0, 4.0), _day(1, 10.0)], [1])
        # 4 bps of 100 is 0.04: bid 99.98, ask 100.02 — day 1's own 10 bps is not used
        assert modelled.tolist() == [True]
        assert bid[0] == pytest.approx(99.98) and ask[0] == pytest.approx(100.02)

    def test_the_first_day_of_the_history_has_nothing_before_it(self):
        _, _, modelled, _ = _quote([_day(0, 4.0)], [0])
        assert modelled.tolist() == [False]

    def test_a_thin_day_is_skipped_for_the_last_usable_one(self):
        days = [_day(0, 4.0), _day(1, 50.0, pairs=MIN_PAIRS_PER_DAY - 1)]
        bid, _, modelled, _ = _quote(days, [2])
        assert modelled.tolist() == [True]
        assert bid[0] == pytest.approx(99.98), 'priced by day 0, not the thin day 1'

    def test_a_gap_longer_than_the_fallback_leaves_the_tick_as_recorded(self):
        bid, ask, modelled, _ = _quote([_day(0, 4.0)], [FALLBACK_DAYS, FALLBACK_DAYS + 1])
        assert modelled.tolist() == [True, False]
        assert (bid[1], ask[1]) == (100.0, 100.0)


class TestWhatIsNeverTouched:

    def test_factor_zero_returns_the_recorded_quote(self):
        bid, ask, modelled, _ = _quote([_day(0, 4.0)], [1], factor=0.0)
        assert not modelled.any()
        assert (bid[0], ask[0]) == (100.0, 100.0)

    def test_a_tick_that_recorded_a_quote_keeps_it(self):
        bid, ask, modelled, _ = _quote([_day(0, 4.0)], [1, 1], without_quote=[False, True])
        assert modelled.tolist() == [False, True]
        assert (bid[0], ask[0]) == (100.0, 100.0)

    def test_the_factor_scales_the_half_spread(self):
        bid, ask, _, _ = _quote([_day(0, 4.0)], [1], factor=2.0)
        assert (bid[0], ask[0]) == (pytest.approx(99.96), pytest.approx(100.04))


class TestWhatTheRunIsTold:

    def test_modelled_stretches_are_named_by_their_first_and_last_tick(self):
        time_msc = np.array([10, 20, 30, 40, 50], dtype=np.int64)
        modelled = np.array([True, True, False, True, False])
        assert modelled_runs(time_msc, modelled) == ((10, 20), (40, 40))
        assert modelled_runs(time_msc, np.zeros(5, dtype=bool)) == ()

    def test_the_median_is_the_full_spread_in_basis_points(self):
        days = [_day(0, 4.0), _day(1, 8.0)]
        _, _, modelled, used = _quote(days, [1, 2, 2])
        _, half = usable_days(days)
        assert median_spread_bps(half, used, modelled, 1.0) == pytest.approx(8.0)

    def test_the_digest_moves_with_the_factor_and_with_a_day_estimate(self):
        def digest(days, factor):
            _, _, modelled, used = _quote(days, [1], factor=factor)
            usable, half = usable_days(days)
            return day_digest(usable, half, used, modelled, factor, 1)

        base = digest([_day(0, 4.0)], 1.0)
        assert digest([_day(0, 4.0)], 1.0) == base
        assert digest([_day(0, 4.0)], 2.0) != base
        assert digest([_day(0, 5.0)], 1.0) != base
