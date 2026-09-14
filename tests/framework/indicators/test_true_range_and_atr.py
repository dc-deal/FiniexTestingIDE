"""
True range and ATR — and which average the bare name "ATR" implies.

True range exists because a bar's own high-low span understates a move that opened away
from the previous close. The ATR is that range, smoothed — and the smoothing is where this
project was wrong under a right-looking name: it used an EMA of the period where Wilder's
definition, and therefore TA-Lib, TradingView and pandas-ta, use the RMA. At period 14 the
smoothing factors are 0.133 against 0.071, so the figure we published as ATR(14) behaved
roughly like a Wilder ATR(7.5).

Nothing decided on it at runtime (§38 keeps volatility profiles out of the execution path),
so the correction cost scenario-generation reruns rather than run results — but the default
is pinned here so it cannot drift back.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.types.indicator_types import MaType
from python.framework.utils.trading_math.indicators.atr import atr, atr_series
from python.framework.utils.trading_math.indicators.true_range import (
    true_range,
    true_range_series,
)


class TestTrueRangeTakesTheWidestOfThree:
    """Span, reach up from the previous close, reach down from it."""

    def test_an_inside_bar_is_its_own_span(self):
        # Previous close sits inside the bar, so no gap widens anything.
        assert true_range(high=105.0, low=95.0, previous_close=100.0) == pytest.approx(10.0)

    def test_a_gap_up_is_measured_from_the_previous_close(self):
        # Opened 20 above yesterday's close: the real distance travelled is 25, not the
        # bar's own 5.
        assert true_range(high=125.0, low=120.0, previous_close=100.0) == pytest.approx(25.0)

    def test_a_gap_down_is_measured_from_the_previous_close(self):
        assert true_range(high=80.0, low=75.0, previous_close=100.0) == pytest.approx(25.0)

    def test_the_first_bar_of_a_series_is_its_own_span(self):
        # No previous close exists, so there is no gap to account for. Established
        # treatment, and it is why this row is a number rather than NaN.
        frame = pd.DataFrame({'h': [105.0, 110.0], 'l': [95.0, 108.0], 'c': [100.0, 109.0]})
        result = true_range_series(frame.h, frame.l, frame.c)
        assert result.iloc[0] == pytest.approx(10.0)


class TestTheAtrSmoothsTheTrueRange:

    def test_a_constant_range_smooths_to_itself(self):
        # Every bar spans exactly 2.0 with no gaps, so any average of it is 2.0.
        closes = np.full(60, 100.0)
        highs = closes + 1.0
        lows = closes - 1.0
        assert atr(highs, lows, closes, 14) == pytest.approx(2.0)

    def test_an_empty_window_is_zero_rather_than_an_error(self):
        empty = np.array([])
        assert atr(empty, empty, empty, 14) == 0.0


class TestTheBareNameMeansWilder:
    """The default is the contract — a caller that does not choose gets the standard."""

    @pytest.fixture
    def bars(self):
        rng = np.random.default_rng(11)
        closes = 100.0 + np.cumsum(rng.normal(0, 0.5, 120))
        return closes + np.abs(rng.normal(0, 0.3, 120)), closes - np.abs(
            rng.normal(0, 0.3, 120)), closes

    def test_the_default_is_wilders_smoothing(self, bars):
        highs, lows, closes = bars
        assert atr(highs, lows, closes, 14) == pytest.approx(
            atr(highs, lows, closes, 14, smoothing=MaType.RMA)
        )

    def test_the_ema_variant_is_reachable_and_different(self, bars):
        highs, lows, closes = bars
        assert atr(highs, lows, closes, 14, smoothing=MaType.EMA) != pytest.approx(
            atr(highs, lows, closes, 14)
        )

    def test_the_ema_variant_reacts_harder_to_a_spike(self):
        # The concrete consequence of the wrong default: one violent bar lifts the EMA
        # variant further, so quiet stretches next to it look calmer by comparison and a
        # volatility split cuts in different places.
        closes = np.full(80, 100.0)
        highs = closes + 1.0
        lows = closes - 1.0
        highs[-1] = 150.0
        lows[-1] = 50.0

        wilder = atr(highs, lows, closes, 14)
        exponential = atr(highs, lows, closes, 14, smoothing=MaType.EMA)
        assert exponential > wilder


class TestTheSeriesFormSaysWhenItCannotAnswer:

    def test_rows_before_the_seed_are_not_a_number(self):
        frame = pd.DataFrame({
            'h': np.full(30, 101.0), 'l': np.full(30, 99.0), 'c': np.full(30, 100.0),
        })
        result = atr_series(frame.h, frame.l, frame.c, 14)
        assert result.iloc[:13].isna().all()
        assert result.iloc[13:].notna().all()
