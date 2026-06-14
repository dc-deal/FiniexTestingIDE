"""
FiniexTestingIDE - Normalizer Tests

Tests the central normalization apparatus (rescale / clamp / normalize) —
the one audited path for cross-instrument-comparable ratios used by the
Bollinger and ma_trend workers.
"""

import pytest

from python.framework.utils.trading_math.normalizer import Normalizer


class TestRescale:
    """rescale(value, lower, upper) — MinMax / %B, unclamped."""

    def test_value_at_lower_is_zero(self):
        assert Normalizer.rescale(10.0, 10.0, 20.0) == 0.0

    def test_value_at_upper_is_one(self):
        assert Normalizer.rescale(20.0, 10.0, 20.0) == 1.0

    def test_value_at_midpoint(self):
        assert Normalizer.rescale(15.0, 10.0, 20.0) == pytest.approx(0.5)

    def test_overshoot_above_upper_exceeds_one(self):
        # Unclamped: overshoot information preserved
        assert Normalizer.rescale(25.0, 10.0, 20.0) == pytest.approx(1.5)

    def test_overshoot_below_lower_is_negative(self):
        assert Normalizer.rescale(5.0, 10.0, 20.0) == pytest.approx(-0.5)

    def test_degenerate_range_returns_midpoint(self):
        # upper == lower → neutral 0.5 (no width to position within)
        assert Normalizer.rescale(10.0, 10.0, 10.0) == 0.5

    def test_inverted_range_returns_midpoint(self):
        # upper < lower → also degenerate
        assert Normalizer.rescale(15.0, 20.0, 10.0) == 0.5


class TestClamp:
    """clamp(x, low, high) — bound into a range."""

    def test_within_range_unchanged(self):
        assert Normalizer.clamp(0.5) == 0.5

    def test_below_low_clamped(self):
        assert Normalizer.clamp(-0.3) == 0.0

    def test_above_high_clamped(self):
        assert Normalizer.clamp(1.4) == 1.0

    def test_at_bounds(self):
        assert Normalizer.clamp(0.0) == 0.0
        assert Normalizer.clamp(1.0) == 1.0

    def test_custom_range(self):
        assert Normalizer.clamp(5.0, low=-2.0, high=2.0) == 2.0
        assert Normalizer.clamp(-5.0, low=-2.0, high=2.0) == -2.0
        assert Normalizer.clamp(1.0, low=-2.0, high=2.0) == 1.0


class TestNormalize:
    """normalize(value, scale) — express value in units of a reference scale."""

    def test_normal_ratio(self):
        assert Normalizer.normalize(2.0, 4.0) == pytest.approx(0.5)

    def test_preserves_sign(self):
        assert Normalizer.normalize(-2.0, 4.0) == pytest.approx(-0.5)

    def test_zero_scale_returns_zero(self):
        # Flat / undefined volatility → safe 0.0
        assert Normalizer.normalize(5.0, 0.0) == 0.0

    def test_negative_scale_returns_zero(self):
        assert Normalizer.normalize(5.0, -3.0) == 0.0

    def test_zero_value_is_zero(self):
        assert Normalizer.normalize(0.0, 4.0) == 0.0
