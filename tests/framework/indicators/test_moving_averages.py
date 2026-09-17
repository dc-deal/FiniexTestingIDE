"""
The three moving averages — one name, one definition each.

SMA, EMA and RMA are not variants of one average with a tuning knob; they are three
measures the industry names separately (pandas-ta `mamode`, TradingView ta.sma / ta.ema /
ta.rma, TA-Lib's own functions). Before this module the project held two EMAs that seeded
differently and disagreed, and the disagreement was invisible because both were called
"the EMA".

Two properties are pinned here that no caller happens to exercise and that cost real money
to rediscover:

    an SMA has NO memory     → a window of exactly `period` is all it ever needs
    an EMA and an RMA DO     → a window of exactly `period` collapses them into their own
                               seed, which IS the SMA, and the option silently does nothing

The second is why `moving_average_warmup_bars` exists and why callers must ask it instead
of passing their period straight through.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.types.indicator_types import MaType
from python.framework.utils.trading_math.indicators.exponential_moving_average import (
    ema,
    ema_series,
    ema_warmup_bars,
)
from python.framework.utils.trading_math.indicators.moving_average import (
    moving_average,
    moving_average_series,
    moving_average_warmup_bars,
)
from python.framework.utils.trading_math.indicators.simple_moving_average import (
    sma,
    sma_series,
)
from python.framework.utils.trading_math.indicators.wilder_moving_average import (
    rma,
    rma_series,
    rma_warmup_bars,
)

_RAMP = np.arange(1.0, 11.0)          # 1 … 10
_FLAT = np.full(20, 42.0)


class TestTheSimpleMovingAverageHasNoMemory:
    """Only the trailing window exists for an SMA. Everything before it is irrelevant."""

    def test_it_averages_the_trailing_window(self):
        # mean(6,7,8,9,10)
        assert sma(_RAMP, 5) == pytest.approx(8.0)

    def test_history_before_the_window_changes_nothing(self):
        extended = np.concatenate([np.full(50, -1000.0), _RAMP])
        assert sma(extended, 5) == pytest.approx(sma(_RAMP, 5))

    def test_a_window_shorter_than_the_period_is_averaged_whole(self):
        # The warmup contract decides how much history exists; raising mid-tick would
        # turn a thin warmup into a dead session.
        assert sma(np.array([2.0, 4.0]), 10) == pytest.approx(3.0)


class TestTheExponentialMovingAverage:
    """alpha = 2/(period+1), seeded on the SMA of the first `period` values."""

    def test_it_matches_the_definition_by_hand(self):
        # seed = mean(1,2,3) = 2, alpha = 0.5, then 4…10 halve the gap each step:
        # 3, 4, 5, 6, 7, 8, 9
        assert ema(_RAMP, 3) == pytest.approx(9.0)

    def test_a_flat_series_stays_at_its_level(self):
        assert ema(_FLAT, 5) == pytest.approx(42.0)

    def test_a_window_of_exactly_the_period_collapses_into_the_sma(self):
        # THE degeneracy. Nothing is left to recurse over, so the EMA returns its own
        # seed — and an `ma_type: ema` setting silently stops meaning anything.
        window = _RAMP[:5]
        assert ema(window, 5) == pytest.approx(sma(window, 5))

    def test_on_a_straight_line_it_equals_the_trailing_sma(self):
        # Worth pinning because it is a trap for the next test writer, not a curiosity:
        # at steady state both averages lag a constant slope by exactly (period-1)/2, so
        # ANY linear series makes the two indistinguishable. Two attempts at an
        # "ema != sma" test picked linear data and passed for the wrong reason.
        ramp = np.arange(1.0, 21.0)
        assert ema(ramp, 5) == pytest.approx(sma(ramp, 5))

    def test_a_curve_separates_it_from_the_sma(self):
        # A step is the cheapest non-linear case: the EMA is still climbing out of the
        # old level while the trailing SMA has already forgotten it.
        step = np.concatenate([np.full(10, 10.0), np.full(10, 20.0)])
        assert ema(step, 5) < sma(step, 5)


class TestTheHandCalculatedEmaReferences:
    """
    Four references carried over from the MACD worker's suite, where they used to reach
    into a private method to test an EMA the worker held privately. The EMA moved here;
    the arithmetic behind the numbers did not, and it is worth keeping written out.
    """

    @pytest.mark.parametrize('prices, period, expected, why', [
        # No iteration left after the seed — the EMA IS its SMA seed.
        ([100.0, 102.0, 104.0], 3, 102.0, 'window equals period'),
        # Too short to seed at all, so the plain mean stands in.
        ([100.0, 102.0], 5, 101.0, 'window shorter than period'),
        # seed 102.0, multiplier 0.5 → 102.5 → 103.75 → 105.375
        ([100.0, 102.0, 104.0, 103.0, 105.0, 107.0], 3, 105.375, 'three iterations'),
        # seed 102.8, multiplier 1/3 → 104.2 → 104.8
        ([100.0, 102.0, 104.0, 103.0, 105.0, 107.0, 106.0], 5, 104.8, 'a different multiplier'),
    ])
    def test_it_matches_the_hand_calculation(self, prices, period, expected, why):
        assert ema(np.array(prices), period) == pytest.approx(expected, abs=0.01), why


class TestWildersMovingAverage:
    """alpha = 1/period — the smoothing ATR and RSI mean by 'average'."""

    def test_it_matches_the_recurrence_by_hand(self):
        seed = float(np.mean(_RAMP[:3]))
        expected = seed
        for value in _RAMP[3:]:
            expected = (value - expected) * (1.0 / 3) + expected
        assert rma(_RAMP, 3) == pytest.approx(expected)

    def test_a_flat_series_stays_at_its_level(self):
        assert rma(_FLAT, 5) == pytest.approx(42.0)

    def test_it_reacts_more_slowly_than_the_ema(self):
        # The defining difference, and the reason our ATR(14) read like a Wilder ATR(7.5)
        # while it was smoothed as an EMA. A step up: the faster average ends higher.
        step = np.concatenate([np.full(30, 10.0), np.full(30, 20.0)])
        assert rma(step, 14) < ema(step, 14)

    def test_a_window_of_exactly_the_period_collapses_into_the_sma(self):
        window = _RAMP[:5]
        assert rma(window, 5) == pytest.approx(sma(window, 5))


class TestTheWarmupContract:
    """How much history each average needs before its seed stops showing."""

    def test_the_sma_needs_exactly_its_period(self):
        assert moving_average_warmup_bars(20, MaType.SMA) == 20

    @pytest.mark.parametrize('period', [2, 5, 14, 20, 50])
    def test_the_recursive_averages_need_a_multiple(self, period):
        assert moving_average_warmup_bars(period, MaType.EMA) == ema_warmup_bars(period)
        assert moving_average_warmup_bars(period, MaType.RMA) == rma_warmup_bars(period)
        assert ema_warmup_bars(period) > period
        assert rma_warmup_bars(period) > ema_warmup_bars(period)

    @pytest.mark.parametrize('ma_type', [MaType.EMA, MaType.RMA])
    @pytest.mark.parametrize('period', [2, 5, 14, 20, 50])
    def test_the_warmup_leaves_the_seed_under_two_percent(self, ma_type, period):
        """
        The factors 3 and 5 are a claim about seed decay, so measure it rather than
        trust it: move ONLY the seed region by a known amount and see how much of that
        move survives to the newest value. Both factors are chosen to land on the same
        residual, which is why one bound covers both averages.
        """
        bars = moving_average_warmup_bars(period, ma_type)
        shift = 1000.0

        base = np.full(bars, 100.0)
        perturbed = base.copy()
        perturbed[:period] += shift

        residual = abs(
            moving_average(perturbed, period, ma_type)
            - moving_average(base, period, ma_type)
        )
        assert residual < 0.02 * shift


class TestTheSelector:
    """`moving_average` picks by name and adds nothing of its own."""

    @pytest.mark.parametrize('ma_type, direct', [
        (MaType.SMA, sma),
        (MaType.EMA, ema),
        (MaType.RMA, rma),
    ])
    def test_it_dispatches_to_the_named_average(self, ma_type, direct):
        assert moving_average(_RAMP, 3, ma_type) == pytest.approx(direct(_RAMP, 3))

    @pytest.mark.parametrize('ma_type, direct', [
        (MaType.SMA, sma_series),
        (MaType.EMA, ema_series),
        (MaType.RMA, rma_series),
    ])
    def test_the_series_form_dispatches_too(self, ma_type, direct):
        series = pd.Series(_RAMP)
        pd.testing.assert_series_equal(
            moving_average_series(series, 3, ma_type), direct(series, 3)
        )


class TestTheSeriesFormsSayWhenTheyCannotAnswer:
    """A row before the window is full is NaN, never a number built from less."""

    @pytest.mark.parametrize('ma_type', [MaType.SMA, MaType.EMA, MaType.RMA])
    def test_rows_before_the_seed_are_not_a_number(self, ma_type):
        result = moving_average_series(pd.Series(_RAMP), 4, ma_type)
        assert result.iloc[:3].isna().all()
        assert result.iloc[3:].notna().all()

    @pytest.mark.parametrize('ma_type', [MaType.EMA, MaType.RMA])
    def test_a_series_too_short_to_seed_is_entirely_nan(self, ma_type):
        result = moving_average_series(pd.Series([1.0, 2.0]), 10, ma_type)
        assert result.isna().all()
