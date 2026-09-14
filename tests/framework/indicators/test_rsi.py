"""
RSI — and the fact that this project computed a different, also-named indicator.

Wilder smoothed both the average gain and the average loss with his own RMA. Averaging
them with a plain arithmetic mean instead yields Cutler's RSI: a real indicator with a real
name, which reacts faster and is not what a reader comparing against a chart expects.

The project computed Cutler's under the plain name until 2026-09-14, and it did so
structurally rather than by choice: the worker handed the averaging exactly `period` deltas,
and an RMA over exactly its own period has nothing to recurse over and collapses into the
simple mean it was seeded with. Both halves are pinned here — the default, and the collapse
that made the deviation invisible.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.types.indicator_types import MaType
from python.framework.utils.trading_math.indicators.rsi import rsi, rsi_series
from python.framework.utils.trading_math.indicators.wilder_moving_average import (
    rma_warmup_bars,
)


class TestTheBoundsOfTheOscillator:

    def test_a_series_that_only_rises_is_fully_overbought(self):
        assert rsi(np.arange(1.0, 40.0), 14).value == pytest.approx(100.0)

    def test_a_flat_series_has_no_losses_to_divide_by(self):
        # avg_loss is zero, so the strength ratio is undefined. 100 is the established
        # answer, and it is the one the project already gave.
        assert rsi(np.full(40, 100.0), 14).value == pytest.approx(100.0)

    def test_mirroring_a_series_mirrors_the_oscillator(self):
        # Exact rather than approximate: reflecting the price path negates every delta, so
        # the two averages swap and the strength ratio inverts. A cleaner statement of
        # "balanced sits at the midpoint" than any hand-built alternating series, which
        # under Wilder smoothing lands slightly off 50 depending on where it ends.
        rng = np.random.default_rng(31)
        closes = 100.0 + np.cumsum(rng.normal(0, 0.7, 120))
        mirrored = 2 * closes[0] - closes

        assert rsi(closes, 14).value + rsi(mirrored, 14).value == pytest.approx(100.0)

    def test_gains_matching_losses_sit_near_the_midpoint(self):
        alternating = np.array([100.0, 101.0] * 40)
        assert rsi(alternating, 14).value == pytest.approx(50.0, abs=5.0)

    def test_it_reports_the_two_averages_it_is_built_from(self):
        result = rsi(np.arange(1.0, 40.0), 14)
        assert result.avg_gain > 0
        assert result.avg_loss == pytest.approx(0.0)

    def test_a_window_with_no_movement_to_measure_is_neutral(self):
        # Unreachable through a worker, whose warmup contract guarantees a window.
        assert rsi(np.array([100.0]), 14).value == pytest.approx(50.0)


class TestTheBareNameMeansWilder:

    @pytest.fixture
    def closes(self):
        rng = np.random.default_rng(23)
        return 100.0 + np.cumsum(rng.normal(0, 0.6, rma_warmup_bars(14) + 40))

    def test_the_default_is_wilders_smoothing(self, closes):
        assert rsi(closes, 14).value == pytest.approx(
            rsi(closes, 14, smoothing=MaType.RMA).value
        )

    def test_cutlers_variant_is_reachable_and_different(self, closes):
        # Named, so the deviation is a declared choice rather than an accident.
        cutlers = rsi(closes, 14, smoothing=MaType.SMA).value
        assert cutlers != pytest.approx(rsi(closes, 14).value)

    def test_exactly_one_period_of_deltas_makes_wilder_into_cutlers(self, closes):
        # THE mechanism behind the old deviation: with `period` deltas the RMA returns its
        # own seed, which is the simple mean — so the two smoothings become the same
        # number and the choice stops existing.
        short = closes[-15:]          # 15 closes → 14 deltas → exactly one period
        assert rsi(short, 14).value == pytest.approx(
            rsi(short, 14, smoothing=MaType.SMA).value
        )

    def test_enough_history_separates_them_again(self, closes):
        assert rsi(closes, 14).value != pytest.approx(
            rsi(closes, 14, smoothing=MaType.SMA).value
        )


class TestTheSeriesForm:

    def test_rows_before_the_seed_are_not_a_number(self):
        rng = np.random.default_rng(5)
        closes = pd.Series(100.0 + np.cumsum(rng.normal(0, 0.5, 60)))
        result = rsi_series(closes, 14)
        # One row for the missing first delta, then the seed needs a full period.
        assert result.iloc[:14].isna().all()
        assert result.iloc[14:].notna().all()

    def test_it_stays_inside_the_oscillator_bounds(self):
        rng = np.random.default_rng(9)
        closes = pd.Series(100.0 + np.cumsum(rng.normal(0, 1.0, 300)))
        result = rsi_series(closes, 14).dropna()
        assert result.between(0.0, 100.0).all()
