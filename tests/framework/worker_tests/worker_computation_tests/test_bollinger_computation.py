"""
FiniexTestingIDE - Bollinger Worker Computation Tests

Tests the Bollinger/Bollinger Band compute() method against hand-calculated values.

Key implementation details (verified from source):
- middle = np.mean(close_prices[-period:])
- std_dev = np.std(close_prices) → POPULATION std dev (ddof=0, NOT sample!)
- upper = middle + std_dev * deviation
- lower = middle - std_dev * deviation
- position = (tick.mid - lower) / (upper - lower), clamped [0, 1]
- Returns WorkerResult with outputs dict {upper, middle, lower, position, std_dev, bars_used}

Reference formula for std_dev (population):
    std = sqrt(sum((x - mean)²) / N)   (NOT N-1!)
"""

import math

import pytest

from python.framework.workers.core.bollinger_worker import BollingerWorker
from python.framework.types.worker_types import WorkerResult

from conftest import make_bars, make_tick


# ============================================
# Pre-calculated constants for closes = [100, 101, 102, 103, 104]
#
# mean = 102.0
# population_std = sqrt(((−2)² + (−1)² + 0² + 1² + 2²) / 5)
#                = sqrt(10 / 5) = sqrt(2) ≈ 1.41421356
# ============================================
EXPECTED_MIDDLE = 102.0
EXPECTED_STD = math.sqrt(2.0)  # 1.41421356...
STANDARD_CLOSES = [100, 101, 102, 103, 104]


class TestBollingerBasicComputation:
    """Test bollinger band calculation against hand-computed values."""

    def test_bollinger_bands_default_deviation(self, mock_logger):
        """
        Bollinger with deviation=2.0 (default).

        closes = [100, 101, 102, 103, 104], period = 5
        middle = 102.0
        std = sqrt(2) ≈ 1.41421
        upper = 102.0 + 1.41421 * 2.0 = 104.82842
        lower = 102.0 - 1.41421 * 2.0 = 99.17157
        """
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)  # mid ≈ 102.0001

        result = worker.compute(
            tick=tick,
            bar_history={"M5": bars},
            current_bars={},
        )

        assert isinstance(result, WorkerResult)
        assert isinstance(result.outputs, dict)

        expected_upper = EXPECTED_MIDDLE + EXPECTED_STD * 2.0
        expected_lower = EXPECTED_MIDDLE - EXPECTED_STD * 2.0

        assert result.get_signal('middle') == pytest.approx(EXPECTED_MIDDLE, abs=0.001)
        assert result.get_signal('upper') == pytest.approx(expected_upper, abs=0.001)
        assert result.get_signal('lower') == pytest.approx(expected_lower, abs=0.001)

    def test_bollinger_bands_custom_deviation(self, mock_logger):
        """
        Bollinger with deviation=1.0 → narrower bands.

        upper = 102.0 + 1.41421 * 1.0 = 103.41421
        lower = 102.0 - 1.41421 * 1.0 = 100.58578
        """
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 1.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        expected_upper = EXPECTED_MIDDLE + EXPECTED_STD * 1.0
        expected_lower = EXPECTED_MIDDLE - EXPECTED_STD * 1.0

        assert result.get_signal('upper') == pytest.approx(expected_upper, abs=0.001)
        assert result.get_signal('lower') == pytest.approx(expected_lower, abs=0.001)

    def test_bollinger_output_keys(self, mock_logger):
        """Result outputs must contain schema-declared keys."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        expected_keys = {
            'upper', 'middle', 'lower', 'position', 'position_raw',
            'slope', 'width_pct', 'std_dev', 'bars_used',
        }
        assert set(result.outputs.keys()) == expected_keys


class TestBollingerPosition:
    """Test position calculation relative to bands."""

    def test_position_at_middle(self, mock_logger):
        """
        Tick at the exact middle → position = 0.5.

        tick.mid = 102.0 (= middle)
        position = (102.0 - lower) / (upper - lower) = 0.5
        """
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        # bid=101.9999, ask=102.0001 → mid = 102.0
        tick = make_tick(bid=101.9999, ask=102.0001)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('position') == pytest.approx(0.5, abs=0.01)

    def test_position_above_upper_clamped(self, mock_logger):
        """
        Tick above upper band → position clamped to 1.0.

        upper ≈ 104.828, tick.mid = 110.0 → raw position > 1.0 → 1.0
        """
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=110.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('position') == 1.0

    def test_position_below_lower_clamped(self, mock_logger):
        """
        Tick below lower band → position clamped to 0.0.

        lower ≈ 99.171, tick.mid = 95.0 → raw position < 0.0 → 0.0
        """
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=95.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('position') == 0.0


class TestBollingerOutputFields:
    """Test bollinger output fields via get_signal()."""

    def test_bollinger_std_dev_output(self, mock_logger):
        """std_dev output must match hand-calculated population std dev."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('std_dev') == pytest.approx(EXPECTED_STD, abs=0.001)
        assert result.get_signal('bars_used') == 5


class TestBollingerRegression:
    """Regression tests for the deviation bug (0.02 vs 2.0)."""

    def test_band_width_sanity_check(self, mock_logger):
        """
        THE BOLLINGER BUG regression test.

        With deviation=2.0 and std≈1.414:
            band_width = upper - lower = 2 * std * deviation
                       = 2 * 1.414 * 2.0 = 5.656

        The old bug had deviation=0.02 which would produce:
            band_width = 2 * 1.414 * 0.02 = 0.0566

        This test ensures the band width is sensible (> 1.0).
        """
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        band_width = result.get_signal('upper') - result.get_signal('lower')
        expected_width = 2 * EXPECTED_STD * 2.0  # ≈ 5.656

        assert band_width == pytest.approx(expected_width, abs=0.01)
        assert band_width > 1.0, (
            f"Band width {band_width:.4f} is suspiciously narrow. "
            f"Possible deviation bug (0.02 instead of 2.0)?"
        )

    def test_constant_prices_zero_std(self, mock_logger):
        """
        All identical close prices → std=0, upper==lower==middle.

        position defaults to 0.5 when upper==lower (division by zero guard).
        """
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars([100.0, 100.0, 100.0, 100.0, 100.0])
        tick = make_tick(bid=100.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('middle') == pytest.approx(100.0, abs=0.001)
        assert result.get_signal('upper') == pytest.approx(100.0, abs=0.001)
        assert result.get_signal('lower') == pytest.approx(100.0, abs=0.001)
        # When upper == lower, position = 0.5 (default)
        assert result.get_signal('position') == pytest.approx(0.5, abs=0.01)


# Six rising closes → period 5 leaves one extra bar for the slope window.
RISING_CLOSES_6 = [100, 101, 102, 103, 104, 105]


class TestBollingerPositionRaw:
    """Test the unclamped position_raw output (overshoot information)."""

    def test_position_raw_above_upper_unclamped(self, mock_logger):
        """Tick above the upper band → position_raw > 1.0 while position clamps to 1.0."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )
        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=110.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('position_raw') > 1.0
        assert result.get_signal('position') == 1.0

    def test_position_raw_below_lower_unclamped(self, mock_logger):
        """Tick below the lower band → position_raw < 0.0 while position clamps to 0.0."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )
        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=95.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('position_raw') < 0.0
        assert result.get_signal('position') == 0.0

    def test_position_raw_equals_position_inside_bands(self, mock_logger):
        """Inside the bands the two coincide (no clamping applied)."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )
        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=101.9999, ask=102.0001)  # mid = 102.0 = middle

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('position_raw') == pytest.approx(
            result.get_signal('position'), abs=0.0001
        )


class TestBollingerSlopeAndWidth:
    """Test the slope and width_pct outputs."""

    def test_slope_positive_on_rising_closes(self, mock_logger):
        """A rising midline yields a positive normalized slope."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CLOSES_6)
        tick = make_tick(bid=105.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('slope') > 0.0

    def test_slope_zero_when_flat(self, mock_logger):
        """Constant closes → zero band width → slope falls back to 0.0."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )
        bars = make_bars([100.0] * 6)
        tick = make_tick(bid=100.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('slope') == 0.0

    def test_slope_zero_without_extra_bar(self, mock_logger):
        """Exactly `period` bars → no previous window → slope fallback 0.0."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )
        bars = make_bars(STANDARD_CLOSES)  # 5 bars, period 5
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('slope') == 0.0

    def test_width_pct_matches_band_width_over_middle(self, mock_logger):
        """width_pct = (upper - lower) / middle."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )
        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        band_width = result.get_signal('upper') - result.get_signal('lower')
        expected = band_width / result.get_signal('middle')
        assert result.get_signal('width_pct') == pytest.approx(expected, abs=0.0001)

    def test_width_pct_zero_when_flat(self, mock_logger):
        """Constant closes → zero band width → width_pct = 0.0."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )
        bars = make_bars([100.0] * 6)
        tick = make_tick(bid=100.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('width_pct') == 0.0


class TestBollingerMaType:
    """Test the ma_type parameter (sma default, ema variant)."""

    def test_default_ma_type_is_sma(self, mock_logger):
        """No ma_type → SMA midline = arithmetic mean of the window."""
        worker = BollingerWorker(
            name="test_bollinger",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )
        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('middle') == pytest.approx(EXPECTED_MIDDLE, abs=0.001)

    def test_ema_midline_differs_from_sma_on_trend(self, mock_logger):
        """On rising closes the EMA midline weights recent prices → above the SMA."""
        sma_worker = BollingerWorker(
            name="test_sma",
            parameters={"periods": {"M5": 5}, "deviation": 2.0, "ma_type": "sma"},
            logger=mock_logger,
        )
        ema_worker = BollingerWorker(
            name="test_ema",
            parameters={"periods": {"M5": 5}, "deviation": 2.0, "ma_type": "ema"},
            logger=mock_logger,
        )
        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        sma_mid = sma_worker.compute(
            tick=tick, bar_history={"M5": bars}, current_bars={}
        ).get_signal('middle')
        ema_mid = ema_worker.compute(
            tick=tick, bar_history={"M5": bars}, current_bars={}
        ).get_signal('middle')

        assert ema_mid != pytest.approx(sma_mid, abs=0.001)
        assert ema_mid > sma_mid  # rising series → EMA leans toward recent highs
