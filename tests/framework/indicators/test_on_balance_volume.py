"""
On-balance volume — volume signed by the direction of the close.

The total starts at zero at the beginning of the window rather than at some inception, so
the LEVEL carries no meaning on its own and only its direction over the window is readable.
That is deliberate, and it is why the worker above reports a trend rather than a value.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.utils.trading_math.indicators.on_balance_volume import (
    obv,
    obv_series,
)


class TestVolumeIsSignedByTheClose:

    def test_a_rising_close_adds_its_volume(self):
        assert obv(np.array([10.0, 11.0, 12.0]), np.array([5.0, 7.0, 9.0])) == (
            pytest.approx(16.0)
        )

    def test_a_falling_close_subtracts_its_volume(self):
        assert obv(np.array([12.0, 11.0, 10.0]), np.array([5.0, 7.0, 9.0])) == (
            pytest.approx(-16.0)
        )

    def test_an_unchanged_close_leaves_the_total_alone(self):
        assert obv(np.array([10.0, 10.0, 10.0]), np.array([5.0, 7.0, 9.0])) == (
            pytest.approx(0.0)
        )

    def test_the_first_bar_has_no_predecessor_and_no_effect(self):
        # Its volume can never be signed, so it never enters the total.
        with_big_first = obv(np.array([10.0, 11.0]), np.array([9999.0, 7.0]))
        with_small_first = obv(np.array([10.0, 11.0]), np.array([1.0, 7.0]))
        assert with_big_first == pytest.approx(with_small_first)

    def test_a_window_too_short_to_compare_is_zero(self):
        assert obv(np.array([10.0]), np.array([5.0])) == 0.0


class TestTheSeriesForm:

    def test_it_starts_at_zero(self):
        result = obv_series(pd.Series([10.0, 11.0, 12.0]), pd.Series([5.0, 7.0, 9.0]))
        assert result.iloc[0] == pytest.approx(0.0)

    def test_it_accumulates_to_the_same_total(self):
        closes = pd.Series([10.0, 11.0, 10.5, 12.0, 12.0])
        volumes = pd.Series([5.0, 7.0, 9.0, 3.0, 4.0])
        assert obv_series(closes, volumes).iloc[-1] == pytest.approx(
            obv(closes.to_numpy(), volumes.to_numpy())
        )
