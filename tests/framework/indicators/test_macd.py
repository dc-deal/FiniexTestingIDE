"""
MACD — and the part that is silent when it is wrong: where the signal line is seeded.

The MACD line only exists from the bar where the SLOW EMA is seeded. Before that both EMAs
are still falling back to a plain mean of the same window, so they cancel exactly and the
"MACD" there is not merely imprecise — it is structurally zero. A signal line seeded on
those values is seeded on artefacts, and nothing about the output looks wrong.

Measured 2026-09-14 on the construction MacdWorker still carries: with periods 35/12/26/9,
four of the nine values feeding the signal EMA's seed were exactly zero and five more were
built from a slow "EMA" that was really a plain mean. This unit builds the standard way
instead, and the test below has teeth against the other one.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.utils.trading_math.indicators.macd import macd, macd_series

_FAST, _SLOW, _SIGNAL = 12, 26, 9


@pytest.fixture
def closes():
    rng = np.random.default_rng(41)
    return 100.0 + np.cumsum(rng.normal(0, 0.5, 200))


class TestTheIdentitiesThatMustHold:

    def test_the_histogram_is_the_gap_between_the_two_lines(self, closes):
        values = macd(closes, _FAST, _SLOW, _SIGNAL)
        assert values.histogram == pytest.approx(values.macd - values.signal)

    def test_the_macd_line_is_the_gap_between_the_two_averages(self, closes):
        values = macd(closes, _FAST, _SLOW, _SIGNAL)
        assert values.macd == pytest.approx(values.fast_ema - values.slow_ema)

    def test_a_flat_series_has_nothing_to_diverge(self):
        values = macd(np.full(200, 100.0), _FAST, _SLOW, _SIGNAL)
        assert values.macd == pytest.approx(0.0)
        assert values.signal == pytest.approx(0.0)
        assert values.histogram == pytest.approx(0.0)


class TestTheSignalIsSeededOnRealMacdValues:
    """
    The discriminating test. On a one-directional series every real MACD value carries the
    same sign, so a signal line contaminated by structurally-zero values is pulled TOWARDS
    zero and lands outside the range of the values it is supposed to average.
    """

    @pytest.mark.parametrize('direction', [1.0, -1.0])
    def test_it_stays_inside_the_range_of_the_line_it_averages(self, direction):
        closes = 100.0 + direction * np.arange(200.0) * 0.5
        frame = macd_series(pd.Series(closes), _FAST, _SLOW, _SIGNAL)

        line = frame['macd'].dropna()
        signal = macd(closes, _FAST, _SLOW, _SIGNAL).signal

        assert line.min() <= signal <= line.max()

    def test_a_trending_series_keeps_the_signal_on_one_side_of_zero(self):
        # Blunter statement of the same thing: a falling market has no positive MACD
        # values anywhere, so a negative signal is the only honest answer.
        closes = 100.0 - np.arange(200.0) * 0.5
        assert macd(closes, _FAST, _SLOW, _SIGNAL).signal < 0.0


class TestTheSeriesFormSaysWhenItCannotAnswer:

    def test_the_line_begins_where_the_slow_average_is_seeded(self, closes):
        frame = macd_series(pd.Series(closes), _FAST, _SLOW, _SIGNAL)
        assert frame['macd'].iloc[:_SLOW - 1].isna().all()
        assert frame['macd'].iloc[_SLOW - 1:].notna().all()

    def test_the_signal_begins_a_signal_period_after_the_line(self, closes):
        frame = macd_series(pd.Series(closes), _FAST, _SLOW, _SIGNAL)
        first_signal = _SLOW - 1 + _SIGNAL - 1
        assert frame['signal'].iloc[:first_signal].isna().all()
        assert frame['signal'].iloc[first_signal:].notna().all()

    def test_the_histogram_identity_holds_row_by_row(self, closes):
        frame = macd_series(pd.Series(closes), _FAST, _SLOW, _SIGNAL).dropna()
        pd.testing.assert_series_equal(
            frame['histogram'], frame['macd'] - frame['signal'], check_names=False
        )
