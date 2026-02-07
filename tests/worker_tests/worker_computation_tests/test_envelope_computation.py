"""
FiniexTestingIDE - Envelope Worker Computation Tests

Tests the Envelope/Bollinger Band compute() method against hand-calculated values.

Key implementation details (verified from source):
- middle = np.mean(close_prices[-period:])
- std_dev = np.std(close_prices) → POPULATION std dev (ddof=0, NOT sample!)
- upper = middle + std_dev * deviation
- lower = middle - std_dev * deviation
- position = (tick.mid - lower) / (upper - lower), clamped [0, 1]
- Returns WorkerResult with value = dict {upper, middle, lower, position}

Reference formula for std_dev (population):
    std = sqrt(sum((x - mean)²) / N)   (NOT N-1!)
"""

import math

import pytest

from python.framework.workers.core.envelope_worker import EnvelopeWorker
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


class TestEnvelopeBasicComputation:
    """Test envelope band calculation against hand-computed values."""

    def test_envelope_bands_default_deviation(self, mock_logger):
        """
        Envelope with deviation=2.0 (default).

        closes = [100, 101, 102, 103, 104], period = 5
        middle = 102.0
        std = sqrt(2) ≈ 1.41421
        upper = 102.0 + 1.41421 * 2.0 = 104.82842
        lower = 102.0 - 1.41421 * 2.0 = 99.17157
        """
        worker = EnvelopeWorker(
            name="test_envelope",
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
        assert isinstance(result.value, dict)

        expected_upper = EXPECTED_MIDDLE + EXPECTED_STD * 2.0
        expected_lower = EXPECTED_MIDDLE - EXPECTED_STD * 2.0

        assert result.value["middle"] == pytest.approx(EXPECTED_MIDDLE, abs=0.001)
        assert result.value["upper"] == pytest.approx(expected_upper, abs=0.001)
        assert result.value["lower"] == pytest.approx(expected_lower, abs=0.001)

    def test_envelope_bands_custom_deviation(self, mock_logger):
        """
        Envelope with deviation=1.0 → narrower bands.

        upper = 102.0 + 1.41421 * 1.0 = 103.41421
        lower = 102.0 - 1.41421 * 1.0 = 100.58578
        """
        worker = EnvelopeWorker(
            name="test_envelope",
            parameters={"periods": {"M5": 5}, "deviation": 1.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        expected_upper = EXPECTED_MIDDLE + EXPECTED_STD * 1.0
        expected_lower = EXPECTED_MIDDLE - EXPECTED_STD * 1.0

        assert result.value["upper"] == pytest.approx(expected_upper, abs=0.001)
        assert result.value["lower"] == pytest.approx(expected_lower, abs=0.001)

    def test_envelope_value_keys(self, mock_logger):
        """Result value dict must contain exactly: upper, middle, lower, position."""
        worker = EnvelopeWorker(
            name="test_envelope",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        expected_keys = {"upper", "middle", "lower", "position"}
        assert set(result.value.keys()) == expected_keys


class TestEnvelopePosition:
    """Test position calculation relative to bands."""

    def test_position_at_middle(self, mock_logger):
        """
        Tick at the exact middle → position = 0.5.

        tick.mid = 102.0 (= middle)
        position = (102.0 - lower) / (upper - lower) = 0.5
        """
        worker = EnvelopeWorker(
            name="test_envelope",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        # bid=101.9999, ask=102.0001 → mid = 102.0
        tick = make_tick(bid=101.9999, ask=102.0001)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value["position"] == pytest.approx(0.5, abs=0.01)

    def test_position_above_upper_clamped(self, mock_logger):
        """
        Tick above upper band → position clamped to 1.0.

        upper ≈ 104.828, tick.mid = 110.0 → raw position > 1.0 → 1.0
        """
        worker = EnvelopeWorker(
            name="test_envelope",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=110.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value["position"] == 1.0

    def test_position_below_lower_clamped(self, mock_logger):
        """
        Tick below lower band → position clamped to 0.0.

        lower ≈ 99.171, tick.mid = 95.0 → raw position < 0.0 → 0.0
        """
        worker = EnvelopeWorker(
            name="test_envelope",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=95.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value["position"] == 0.0


class TestEnvelopeMetadataAndConfidence:
    """Test metadata and confidence for envelope worker."""

    def test_envelope_metadata_fields(self, mock_logger):
        """Metadata must contain period, timeframe, deviation, std_dev, bars_used."""
        worker = EnvelopeWorker(
            name="test_envelope",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.metadata["period"] == 5
        assert result.metadata["timeframe"] == "M5"
        assert result.metadata["deviation"] == 2.0
        assert result.metadata["bars_used"] == 5
        assert result.metadata["std_dev"] == pytest.approx(EXPECTED_STD, abs=0.001)

    def test_envelope_confidence_partial_data(self, mock_logger):
        """
        Confidence formula: min(1.0, len(bars) / (period * 2))

        5 bars, period=5: confidence = min(1.0, 5/10) = 0.5
        """
        worker = EnvelopeWorker(
            name="test_envelope",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.confidence == pytest.approx(0.5, abs=0.001)


class TestEnvelopeRegression:
    """Regression tests for the deviation bug (0.02 vs 2.0)."""

    def test_band_width_sanity_check(self, mock_logger):
        """
        THE ENVELOPE BUG regression test.

        With deviation=2.0 and std≈1.414:
            band_width = upper - lower = 2 * std * deviation
                       = 2 * 1.414 * 2.0 = 5.656

        The old bug had deviation=0.02 which would produce:
            band_width = 2 * 1.414 * 0.02 = 0.0566

        This test ensures the band width is sensible (> 1.0).
        """
        worker = EnvelopeWorker(
            name="test_envelope",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars(STANDARD_CLOSES)
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        band_width = result.value["upper"] - result.value["lower"]
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
        worker = EnvelopeWorker(
            name="test_envelope",
            parameters={"periods": {"M5": 5}, "deviation": 2.0},
            logger=mock_logger,
        )

        bars = make_bars([100.0, 100.0, 100.0, 100.0, 100.0])
        tick = make_tick(bid=100.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value["middle"] == pytest.approx(100.0, abs=0.001)
        assert result.value["upper"] == pytest.approx(100.0, abs=0.001)
        assert result.value["lower"] == pytest.approx(100.0, abs=0.001)
        # When upper == lower, position = 0.5 (default)
        assert result.value["position"] == pytest.approx(0.5, abs=0.01)
