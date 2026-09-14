"""
Bollinger bands — a midline with a symmetric band drawn from the window's own dispersion.

The midline's average is the caller's choice, which is why the band unit takes MaType
rather than deciding. What it deliberately does NOT do is place a price inside the bands:
expressing a value as a position between two bounds is normalization and belongs to the
Normalizer (§38), which also keeps the clamped and unclamped readings apart.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.types.indicator_types import MaType
from python.framework.utils.trading_math.indicators.bollinger_bands import (
    bollinger_bands,
    bollinger_bands_series,
)
from python.framework.utils.trading_math.indicators.exponential_moving_average import (
    ema_warmup_bars,
)

_PERIOD = 20


@pytest.fixture
def closes():
    rng = np.random.default_rng(17)
    return 100.0 + np.cumsum(rng.normal(0, 0.4, ema_warmup_bars(_PERIOD) + 20))


class TestTheBandIsSymmetricAroundTheMidline:

    def test_both_bands_sit_the_same_distance_out(self, closes):
        bands = bollinger_bands(closes, _PERIOD, 2.0)
        assert bands.upper - bands.middle == pytest.approx(bands.middle - bands.lower)

    def test_the_distance_is_the_deviation_multiple(self, closes):
        bands = bollinger_bands(closes, _PERIOD, 2.0)
        assert bands.upper - bands.middle == pytest.approx(2.0 * bands.std_dev)

    def test_a_wider_deviation_widens_the_band_proportionally(self, closes):
        two = bollinger_bands(closes, _PERIOD, 2.0)
        three = bollinger_bands(closes, _PERIOD, 3.0)
        assert three.upper - three.lower == pytest.approx(
            1.5 * (two.upper - two.lower)
        )

    def test_a_flat_window_collapses_the_band_onto_the_midline(self):
        flat = np.full(60, 100.0)
        bands = bollinger_bands(flat, _PERIOD, 2.0)
        assert bands.std_dev == pytest.approx(0.0)
        assert bands.upper == pytest.approx(bands.middle) == pytest.approx(bands.lower)


class TestTheMidlineIsTheCallersChoice:

    def test_the_default_midline_is_the_simple_average(self, closes):
        assert bollinger_bands(closes, _PERIOD, 2.0).middle == pytest.approx(
            bollinger_bands(closes, _PERIOD, 2.0, ma_type=MaType.SMA).middle
        )

    def test_an_exponential_midline_differs_given_enough_history(self, closes):
        # "Given enough history" is the whole point: with a window of exactly `period`
        # the EMA returns its own SMA seed and the option would do nothing at all.
        assert bollinger_bands(closes, _PERIOD, 2.0, ma_type=MaType.EMA).middle != (
            pytest.approx(bollinger_bands(closes, _PERIOD, 2.0).middle)
        )

    def test_the_dispersion_is_the_same_whichever_midline_is_chosen(self, closes):
        # The band width measures the window, not the midline — so switching the average
        # moves the band but never widens it.
        simple = bollinger_bands(closes, _PERIOD, 2.0, ma_type=MaType.SMA)
        exponential = bollinger_bands(closes, _PERIOD, 2.0, ma_type=MaType.EMA)
        assert simple.std_dev == pytest.approx(exponential.std_dev)


class TestTheSeriesForm:

    def test_it_carries_all_four_columns(self, closes):
        frame = bollinger_bands_series(pd.Series(closes), _PERIOD, 2.0)
        assert list(frame.columns) == ['upper', 'middle', 'lower', 'std_dev']

    def test_rows_before_the_window_is_full_are_not_a_number(self, closes):
        frame = bollinger_bands_series(pd.Series(closes), _PERIOD, 2.0)
        assert frame['middle'].iloc[:_PERIOD - 1].isna().all()
        assert frame['middle'].iloc[_PERIOD - 1:].notna().all()
