"""
The closed form of the EMA/RMA recursion must equal the recursion it replaced.

This is the one unit whose errors would be SILENT. A wrong exponent, a seed term off by one
decay step, weights applied in the wrong direction — none of it raises, none of it produces a
NaN, and every one of them returns a number that looks like a moving average. The tests below
therefore compare against a literal transcription of the recursion rather than against
recorded expectations: an expectation can be regenerated from broken code, a transcription of
the definition cannot.

The scalar/series parity suite pins the same values against pandas' `ewm` — an independent
implementation — so between the two the closed form is checked against both the definition it
came from and a third party.
"""

import numpy as np
import pytest

from python.framework.utils.trading_math.indicators.exponential_moving_average import ema
from python.framework.utils.trading_math.indicators.recursive_average import (
    recursive_average,
)
from python.framework.utils.trading_math.indicators.wilder_moving_average import rma


def _recursion(values: np.ndarray, period: int, alpha: float) -> float:
    """
    The recursion in its plain form, as the definition states it.

    Args:
        values: Value window, oldest first
        period: Seed length
        alpha: Smoothing factor

    Returns:
        The exponential average at the newest value
    """
    if len(values) < period:
        return float(np.mean(values))
    result = float(np.mean(values[:period]))
    for value in values[period:]:
        result = (value - result) * alpha + result
    return float(result)


class TestItEqualsTheRecursionItReplaced:
    """
    Swept rather than spot-checked, because the failure modes are shaped like the input.

    A wrong exponent shows only once the tail is long; a broken seed term shows only once the
    tail is SHORT, where the seed still carries weight; a weight vector applied backwards
    shows only on a series that is not symmetric. One case of each would pass a broken
    implementation, so the sweep crosses period, tail length and price scale.
    """

    @pytest.mark.parametrize('period', [2, 3, 14, 20, 50])
    @pytest.mark.parametrize('extra', [0, 1, 7, 60, 300])
    @pytest.mark.parametrize('base', [0.0001, 100.0, 88_000.0])
    def test_across_period_tail_length_and_price_scale(self, period, extra, base):
        rng = np.random.default_rng(period * 1000 + extra)
        values = rng.normal(base, base * 0.01, period + extra)

        for alpha in (1.0 / period, 2.0 / (period + 1)):
            assert recursive_average(values, period, alpha) == pytest.approx(
                _recursion(values, period, alpha), rel=1e-12)

    def test_a_window_of_exactly_the_period_returns_its_own_seed(self):
        """
        The collapse both named averages document: nothing is left to recurse over.

        Asserted here as well as in their own suites because it is the boundary the closed
        form has to special-case — `beta ** 0 * seed` plus an empty dot product is correct
        arithmetic but reaches it by a different route.
        """
        values = np.array([10.0, 20.0, 30.0, 40.0])

        assert recursive_average(values, 4, 0.25) == pytest.approx(25.0)

    def test_a_window_shorter_than_the_period_falls_back_to_the_plain_mean(self):
        values = np.array([10.0, 20.0, 30.0])

        assert recursive_average(values, 10, 0.1) == pytest.approx(20.0)

    def test_a_single_value_beyond_the_seed_is_one_decay_step(self):
        """One step by hand — the case where an off-by-one in the exponent is visible."""
        values = np.array([10.0, 10.0, 20.0])

        # seed = 10, then (20 - 10) * 0.5 + 10
        assert recursive_average(values, 2, 0.5) == pytest.approx(15.0)


class TestTheNamedAveragesStillCarryTheirOwnSmoothing:
    """
    Sharing the arithmetic must not make the two averages the same function.

    That is the whole point of §46's three named averages: one definition each. If a refactor
    ever passed the wrong alpha through, every value would still look plausible — Wilder's
    would simply have become an EMA.
    """

    def test_wilder_smooths_with_one_over_period(self):
        values = np.arange(100.0)
        period = 14

        assert rma(values, period) == pytest.approx(
            _recursion(values, period, 1.0 / period), rel=1e-12)

    def test_the_ema_smooths_with_two_over_period_plus_one(self):
        values = np.arange(100.0)
        period = 14

        assert ema(values, period) == pytest.approx(
            _recursion(values, period, 2.0 / (period + 1)), rel=1e-12)

    def test_and_the_two_are_not_the_same_number(self):
        values = np.concatenate([np.full(40, 100.0), np.full(40, 110.0)])

        assert rma(values, 14) != pytest.approx(ema(values, 14), rel=1e-3)
