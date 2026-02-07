"""
FiniexTestingIDE - RSI Worker Computation Tests

Tests the RSI compute() method against hand-calculated reference values.

Key implementation details (verified from source):
- SMA-based RSI (NOT Wilder's Exponential Smoothing)
- Uses np.mean(gains) and np.mean(losses) over ALL deltas
- Takes bars[-(period + 1):] → needs period + 1 bars minimum
- Returns WorkerResult with value = float(rsi)

Reference formula:
    deltas = np.diff(close_prices)
    avg_gain = mean(positive deltas, zeros included)
    avg_loss = mean(abs(negative deltas), zeros included)
    RS = avg_gain / avg_loss
    RSI = 100 - (100 / (1 + RS))
    Special case: avg_loss == 0 → RSI = 100.0
"""

import pytest

from python.framework.workers.core.rsi_worker import RSIWorker
from python.framework.types.worker_types import WorkerResult

from conftest import make_bars, make_tick


class TestRSIBasicComputation:
    """Test RSI calculation against hand-computed reference values."""

    def test_rsi_known_values(self, mock_logger):
        """
        RSI with known mixed gains and losses.

        closes = [100, 102, 101, 103, 104], period = 4
        deltas:  [+2,  -1,  +2,  +1]
        gains:   [ 2,   0,   2,   1]  → avg = 5/4 = 1.25
        losses:  [ 0,   1,   0,   0]  → avg = 1/4 = 0.25
        RS = 1.25 / 0.25 = 5.0
        RSI = 100 - 100/(1+5) = 83.333...
        """
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 101, 103, 104])
        tick = make_tick(bid=104.0)

        result = worker.compute(
            tick=tick,
            bar_history={"M5": bars},
            current_bars={},
        )

        assert isinstance(result, WorkerResult)
        assert result.value == pytest.approx(83.333, abs=0.01)

    def test_rsi_all_gains(self, mock_logger):
        """
        Monotonically rising prices → RSI = 100.0.

        closes = [100, 101, 102, 103, 104], period = 4
        deltas:  [+1, +1, +1, +1]
        gains:   [ 1,  1,  1,  1]  → avg = 1.0
        losses:  [ 0,  0,  0,  0]  → avg = 0.0
        avg_loss == 0 → RSI = 100.0 (special case in code)
        """
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        bars = make_bars([100, 101, 102, 103, 104])
        tick = make_tick(bid=104.0)

        result = worker.compute(
            tick=tick,
            bar_history={"M5": bars},
            current_bars={},
        )

        assert result.value == 100.0

    def test_rsi_all_losses(self, mock_logger):
        """
        Monotonically falling prices → RSI = 0.0.

        closes = [104, 103, 102, 101, 100], period = 4
        deltas:  [-1, -1, -1, -1]
        gains:   [ 0,  0,  0,  0]  → avg = 0.0
        losses:  [ 1,  1,  1,  1]  → avg = 1.0
        RS = 0/1 = 0.0
        RSI = 100 - 100/(1+0) = 0.0
        """
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        bars = make_bars([104, 103, 102, 101, 100])
        tick = make_tick(bid=100.0)

        result = worker.compute(
            tick=tick,
            bar_history={"M5": bars},
            current_bars={},
        )

        assert result.value == pytest.approx(0.0, abs=0.01)

    def test_rsi_equal_gains_losses(self, mock_logger):
        """
        Equal average gain and loss → RSI = 50.0 (neutral).

        closes = [100, 102, 100, 102, 100], period = 4
        deltas:  [+2, -2, +2, -2]
        gains:   [ 2,  0,  2,  0]  → avg = 1.0
        losses:  [ 0,  2,  0,  2]  → avg = 1.0
        RS = 1.0
        RSI = 100 - 100/(1+1) = 50.0
        """
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 100, 102, 100])
        tick = make_tick(bid=100.0)

        result = worker.compute(
            tick=tick,
            bar_history={"M5": bars},
            current_bars={},
        )

        assert result.value == pytest.approx(50.0, abs=0.01)


class TestRSIMetadataAndConfidence:
    """Test RSI metadata fields and confidence calculation."""

    def test_rsi_worker_name(self, mock_logger):
        """WorkerResult.worker_name must match the worker instance name."""
        worker = RSIWorker(
            name="my_rsi_instance",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        bars = make_bars([100, 101, 102, 103, 104])
        tick = make_tick(bid=104.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.worker_name == "my_rsi_instance"

    def test_rsi_metadata_fields(self, mock_logger):
        """Metadata must contain period, timeframe, avg_gain, avg_loss, bars_used."""
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 101, 103, 104])
        tick = make_tick(bid=104.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.metadata["period"] == 4
        assert result.metadata["timeframe"] == "M5"
        assert result.metadata["bars_used"] == 5
        assert "avg_gain" in result.metadata
        assert "avg_loss" in result.metadata

    def test_rsi_metadata_gain_loss_values(self, mock_logger):
        """
        Verify avg_gain and avg_loss in metadata match hand calculation.

        closes = [100, 102, 101, 103, 104]
        avg_gain = 5/4 = 1.25
        avg_loss = 1/4 = 0.25
        """
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 101, 103, 104])
        tick = make_tick(bid=104.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.metadata["avg_gain"] == pytest.approx(1.25, abs=0.001)
        assert result.metadata["avg_loss"] == pytest.approx(0.25, abs=0.001)

    def test_rsi_confidence_partial_data(self, mock_logger):
        """
        Confidence formula: min(1.0, len(bars) / (period * 2))

        5 bars, period=4: confidence = min(1.0, 5/8) = 0.625
        """
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 101, 103, 104])
        tick = make_tick(bid=104.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.confidence == pytest.approx(0.625, abs=0.001)

    def test_rsi_confidence_saturates_at_one(self, mock_logger):
        """
        With enough bars, confidence caps at 1.0.

        10 bars, period=4: min(1.0, 10/8) = 1.0
        """
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        closes = [100 + i for i in range(10)]
        bars = make_bars(closes)
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.confidence == 1.0


class TestRSIBoundaryAndRange:
    """Test RSI value bounds and edge cases."""

    def test_rsi_always_between_0_and_100(self, mock_logger):
        """RSI must be in [0, 100] regardless of input."""
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 4}},
            logger=mock_logger,
        )

        # Volatile data with large swings
        bars = make_bars([100, 150, 80, 120, 90])
        tick = make_tick(bid=90.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert 0.0 <= result.value <= 100.0

    def test_rsi_with_large_period(self, mock_logger):
        """RSI with period=14 (standard) and enough data."""
        worker = RSIWorker(
            name="test_rsi",
            parameters={"periods": {"M5": 14}},
            logger=mock_logger,
        )

        # 15 bars needed for period=14
        closes = [
            44.00, 44.34, 44.09, 43.61, 44.33,
            44.83, 45.10, 45.42, 45.84, 46.08,
            45.89, 46.03, 45.61, 46.28, 46.00,
        ]
        bars = make_bars(closes)
        tick = make_tick(bid=46.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        # Must return a valid RSI in range
        assert 0.0 <= result.value <= 100.0
        # This upward-biased series should be above 50
        assert result.value > 50.0
