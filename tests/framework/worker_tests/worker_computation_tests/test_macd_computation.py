"""
FiniexTestingIDE - MACD Worker Computation Tests

Tests the MACD compute() and _calculate_ema() methods.

Key implementation details (verified from source):
- _calculate_ema: Starts with SMA of first `period` values, then iterates
    multiplier = 2 / (period + 1)
    ema = SMA(first period values)
    for each remaining price: ema = (price - ema) * multiplier + ema
- If len(prices) < period → falls back to np.mean(prices)
- MACD line = fast_ema - slow_ema
- Signal line = EMA of historical MACD values (complex loop)
- Histogram = MACD - Signal
- Returns WorkerResult with outputs dict {macd, signal, histogram, fast_ema, slow_ema, bars_used}
"""

import numpy as np
import pytest

from python.framework.workers.core.macd_worker import MacdWorker
from python.framework.types.worker_types import WorkerResult

from conftest import make_bars, make_tick


class TestEMACalculation:
    """
    Test _calculate_ema() directly with hand-computed values.

    This is the foundation - if EMA is correct, MACD follows.
    """

    def _make_macd_worker(self, mock_logger):
        """Helper: create a MACD worker for EMA access."""
        return MacdWorker(
            name="test_macd",
            parameters={
                "periods": {"M5": 10},
                "fast_period": 3,
                "slow_period": 5,
                "signal_period": 2,
            },
            logger=mock_logger,
        )

    def test_ema_exact_period_returns_sma(self, mock_logger):
        """
        When len(prices) == period, EMA = SMA (no iteration step).

        prices = [100, 102, 104], period = 3
        SMA = (100 + 102 + 104) / 3 = 102.0
        """
        worker = self._make_macd_worker(mock_logger)
        prices = np.array([100.0, 102.0, 104.0])

        ema = worker._calculate_ema(prices, period=3)

        assert ema == pytest.approx(102.0, abs=0.001)

    def test_ema_less_than_period_returns_mean(self, mock_logger):
        """
        When len(prices) < period, falls back to np.mean.

        prices = [100, 102], period = 5
        mean = 101.0
        """
        worker = self._make_macd_worker(mock_logger)
        prices = np.array([100.0, 102.0])

        ema = worker._calculate_ema(prices, period=5)

        assert ema == pytest.approx(101.0, abs=0.001)

    def test_ema_iterative_calculation(self, mock_logger):
        """
        EMA with iteration steps, hand-calculated.

        prices = [100, 102, 104, 103, 105, 107], period = 3
        multiplier = 2 / (3 + 1) = 0.5

        SMA(first 3) = (100 + 102 + 104) / 3 = 102.0
        After 103: (103 - 102.0) * 0.5 + 102.0 = 102.5
        After 105: (105 - 102.5) * 0.5 + 102.5 = 103.75
        After 107: (107 - 103.75) * 0.5 + 103.75 = 105.375
        """
        worker = self._make_macd_worker(mock_logger)
        prices = np.array([100.0, 102.0, 104.0, 103.0, 105.0, 107.0])

        ema = worker._calculate_ema(prices, period=3)

        assert ema == pytest.approx(105.375, abs=0.001)

    def test_ema_period_5(self, mock_logger):
        """
        EMA with period=5, verifying different multiplier.

        prices = [100, 102, 104, 103, 105, 107, 106], period = 5
        multiplier = 2 / (5 + 1) = 0.33333

        SMA(first 5) = (100 + 102 + 104 + 103 + 105) / 5 = 102.8
        After 107: (107 - 102.8) * 0.33333 + 102.8 = 104.2
        After 106: (106 - 104.2) * 0.33333 + 104.2 = 104.8
        """
        worker = self._make_macd_worker(mock_logger)
        prices = np.array([100.0, 102.0, 104.0, 103.0, 105.0, 107.0, 106.0])

        ema = worker._calculate_ema(prices, period=5)

        assert ema == pytest.approx(104.8, abs=0.01)


class TestMACDStructure:
    """Test MACD output structure and key presence."""

    def test_macd_returns_worker_result(self, mock_logger):
        """MACD compute() must return a WorkerResult."""
        worker = MacdWorker(
            name="test_macd",
            parameters={
                "periods": {"M5": 10},
                "fast_period": 3,
                "slow_period": 5,
                "signal_period": 2,
            },
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 104, 103, 105, 107, 106, 108, 110, 109])
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert isinstance(result, WorkerResult)

    def test_macd_output_keys(self, mock_logger):
        """Result outputs dict must contain schema-declared keys."""
        worker = MacdWorker(
            name="test_macd",
            parameters={
                "periods": {"M5": 10},
                "fast_period": 3,
                "slow_period": 5,
                "signal_period": 2,
            },
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 104, 103, 105, 107, 106, 108, 110, 109])
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        expected_keys = {'macd', 'signal', 'histogram', 'fast_ema', 'slow_ema', 'bars_used'}
        assert set(result.outputs.keys()) == expected_keys

    def test_macd_values_are_float(self, mock_logger):
        """All MACD values must be Python floats (not numpy)."""
        worker = MacdWorker(
            name="test_macd",
            parameters={
                "periods": {"M5": 10},
                "fast_period": 3,
                "slow_period": 5,
                "signal_period": 2,
            },
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 104, 103, 105, 107, 106, 108, 110, 109])
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        for key, value in result.outputs.items():
            assert isinstance(value, float), (
                f"MACD value '{key}' is {type(value).__name__}, expected float"
            )

    def test_macd_bars_used_output(self, mock_logger):
        """bars_used output must match input bar count."""
        worker = MacdWorker(
            name="test_macd",
            parameters={
                "periods": {"M5": 10},
                "fast_period": 3,
                "slow_period": 5,
                "signal_period": 2,
            },
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 104, 103, 105, 107, 106, 108, 110, 109])
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.outputs['bars_used'] == 10


class TestMACDDirection:
    """Test MACD line direction based on price trends."""

    def test_macd_rising_prices_positive(self, mock_logger):
        """
        Strongly rising prices → fast_ema > slow_ema → MACD line > 0.

        Fast EMA reacts quicker to the uptrend, overshooting slow EMA.
        """
        worker = MacdWorker(
            name="test_macd",
            parameters={
                "periods": {"M5": 10},
                "fast_period": 3,
                "slow_period": 5,
                "signal_period": 2,
            },
            logger=mock_logger,
        )

        # Clear uptrend
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
        bars = make_bars(closes)
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.outputs["macd"] > 0, (
            f"Rising prices should produce positive MACD, got {result.outputs['macd']}"
        )
        assert result.outputs["fast_ema"] > result.outputs["slow_ema"]

    def test_macd_falling_prices_negative(self, mock_logger):
        """
        Strongly falling prices → fast_ema < slow_ema → MACD line < 0.

        Fast EMA drops quicker, undershooting slow EMA.
        """
        worker = MacdWorker(
            name="test_macd",
            parameters={
                "periods": {"M5": 10},
                "fast_period": 3,
                "slow_period": 5,
                "signal_period": 2,
            },
            logger=mock_logger,
        )

        # Clear downtrend
        closes = [109, 108, 107, 106, 105, 104, 103, 102, 101, 100]
        bars = make_bars(closes)
        tick = make_tick(bid=100.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.outputs["macd"] < 0, (
            f"Falling prices should produce negative MACD, got {result.outputs['macd']}"
        )
        assert result.outputs["fast_ema"] < result.outputs["slow_ema"]

    def test_macd_histogram_equals_macd_minus_signal(self, mock_logger):
        """Histogram must always equal MACD line minus Signal line."""
        worker = MacdWorker(
            name="test_macd",
            parameters={
                "periods": {"M5": 10},
                "fast_period": 3,
                "slow_period": 5,
                "signal_period": 2,
            },
            logger=mock_logger,
        )

        closes = [100, 102, 104, 103, 105, 107, 106, 108, 110, 109]
        bars = make_bars(closes)
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        expected_histogram = result.outputs["macd"] - result.outputs["signal"]
        assert result.outputs["histogram"] == pytest.approx(expected_histogram, abs=0.0001)
