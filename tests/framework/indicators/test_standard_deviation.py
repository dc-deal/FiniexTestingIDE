"""
Windowed standard deviation — the dispersion Bollinger bands are drawn from.

Population (ddof=0), not sample. The distinction is one line of code and a real difference
on short windows: the charting convention treats the window as the population it describes
rather than as a sample drawn from something larger, and a 20-bar band built with ddof=1
would sit about 2.6 % wider than every chart the reader compares it against.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.utils.trading_math.indicators.standard_deviation import (
    window_std,
    window_std_series,
)


class TestItMeasuresTheTrailingWindow:

    def test_a_flat_window_has_no_dispersion(self):
        assert window_std(np.full(20, 42.0), 20) == pytest.approx(0.0)

    def test_it_ignores_history_before_the_window(self):
        values = np.concatenate([np.full(50, -1000.0), np.full(10, 5.0)])
        assert window_std(values, 10) == pytest.approx(0.0)

    def test_it_is_the_population_deviation(self):
        # [2, 4]: population 1.0, sample would be sqrt(2) ~ 1.414.
        assert window_std(np.array([2.0, 4.0]), 2) == pytest.approx(1.0)


class TestTheSeriesForm:

    def test_it_is_the_population_deviation_too(self):
        # The two forms disagreeing on ddof is exactly the kind of split this library
        # exists to prevent, so it is pinned rather than assumed.
        values = pd.Series([2.0, 4.0])
        assert window_std_series(values, 2).iloc[-1] == pytest.approx(1.0)

    def test_rows_before_the_window_is_full_are_not_a_number(self):
        result = window_std_series(pd.Series(np.arange(10.0)), 4)
        assert result.iloc[:3].isna().all()
        assert result.iloc[3:].notna().all()
