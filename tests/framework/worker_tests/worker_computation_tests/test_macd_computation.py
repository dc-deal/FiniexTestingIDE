"""
FiniexTestingIDE - MACD Worker Computation Tests

Tests the MACD compute() method.

Key implementation details (verified from source):
- The EMA itself now lives in trading_math/indicators, and its hand-calculated reference
  values moved to tests/framework/indicators/test_moving_averages.py with it — this suite
  no longer reaches into a private method to test shared arithmetic (§15)
- MACD line = fast_ema - slow_ema
- Signal line = EMA of historical MACD values (complex loop). KNOWN DEVIATION from the
  standard construction, documented at the call site and carried by #517: the loop feeds
  the signal EMA with MACD values taken before the slow EMA is seeded, which are
  structurally zero. Changing it moves MACD output in every backtest, so it is its own
  decision
- Histogram = MACD - Signal
- Returns WorkerResult with outputs dict {macd, signal, histogram, fast_ema, slow_ema, bars_used}
"""

import numpy as np
import pytest
from tests.framework.worker_tests.worker_computation_tests.conftest import make_bars, make_tick

from python.framework.types.worker_types import WorkerResult
from python.framework.workers.core.macd_worker import MacdWorker


class TestMACDStructure:
    """Test MACD output structure and key presence."""

    def test_macd_returns_worker_result(self, mock_logger):
        """MACD compute() must return a WorkerResult."""
        worker = MacdWorker(
            name='test_macd',
            parameters={
                'periods': {'M5': 10},
                'fast_period': 3,
                'slow_period': 5,
                'signal_period': 2,
            },
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 104, 103, 105, 107, 106, 108, 110, 109])
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={'M5': bars}, current_bars={})

        assert isinstance(result, WorkerResult)

    def test_macd_output_keys(self, mock_logger):
        """Result outputs dict must contain schema-declared keys."""
        worker = MacdWorker(
            name='test_macd',
            parameters={
                'periods': {'M5': 10},
                'fast_period': 3,
                'slow_period': 5,
                'signal_period': 2,
            },
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 104, 103, 105, 107, 106, 108, 110, 109])
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={'M5': bars}, current_bars={})

        expected_keys = {'macd', 'signal', 'histogram', 'fast_ema', 'slow_ema', 'bars_used'}
        assert set(result.outputs.keys()) == expected_keys

    def test_macd_values_are_float(self, mock_logger):
        """All MACD values must be Python floats (not numpy)."""
        worker = MacdWorker(
            name='test_macd',
            parameters={
                'periods': {'M5': 10},
                'fast_period': 3,
                'slow_period': 5,
                'signal_period': 2,
            },
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 104, 103, 105, 107, 106, 108, 110, 109])
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={'M5': bars}, current_bars={})

        for key, value in result.outputs.items():
            assert isinstance(value, float), (
                f"MACD value '{key}' is {type(value).__name__}, expected float"
            )

    def test_macd_bars_used_output(self, mock_logger):
        """bars_used output must match input bar count."""
        worker = MacdWorker(
            name='test_macd',
            parameters={
                'periods': {'M5': 10},
                'fast_period': 3,
                'slow_period': 5,
                'signal_period': 2,
            },
            logger=mock_logger,
        )

        bars = make_bars([100, 102, 104, 103, 105, 107, 106, 108, 110, 109])
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={'M5': bars}, current_bars={})

        assert result.outputs['bars_used'] == 10


class TestMACDDirection:
    """Test MACD line direction based on price trends."""

    def test_macd_rising_prices_positive(self, mock_logger):
        """
        Strongly rising prices → fast_ema > slow_ema → MACD line > 0.

        Fast EMA reacts quicker to the uptrend, overshooting slow EMA.
        """
        worker = MacdWorker(
            name='test_macd',
            parameters={
                'periods': {'M5': 10},
                'fast_period': 3,
                'slow_period': 5,
                'signal_period': 2,
            },
            logger=mock_logger,
        )

        # Clear uptrend
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
        bars = make_bars(closes)
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={'M5': bars}, current_bars={})

        assert result.outputs['macd'] > 0, (
            f"Rising prices should produce positive MACD, got {result.outputs['macd']}"
        )
        assert result.outputs['fast_ema'] > result.outputs['slow_ema']

    def test_macd_falling_prices_negative(self, mock_logger):
        """
        Strongly falling prices → fast_ema < slow_ema → MACD line < 0.

        Fast EMA drops quicker, undershooting slow EMA.
        """
        worker = MacdWorker(
            name='test_macd',
            parameters={
                'periods': {'M5': 10},
                'fast_period': 3,
                'slow_period': 5,
                'signal_period': 2,
            },
            logger=mock_logger,
        )

        # Clear downtrend
        closes = [109, 108, 107, 106, 105, 104, 103, 102, 101, 100]
        bars = make_bars(closes)
        tick = make_tick(bid=100.0)

        result = worker.compute(tick=tick, bar_history={'M5': bars}, current_bars={})

        assert result.outputs['macd'] < 0, (
            f"Falling prices should produce negative MACD, got {result.outputs['macd']}"
        )
        assert result.outputs['fast_ema'] < result.outputs['slow_ema']

    def test_macd_histogram_equals_macd_minus_signal(self, mock_logger):
        """Histogram must always equal MACD line minus Signal line."""
        worker = MacdWorker(
            name='test_macd',
            parameters={
                'periods': {'M5': 10},
                'fast_period': 3,
                'slow_period': 5,
                'signal_period': 2,
            },
            logger=mock_logger,
        )

        closes = [100, 102, 104, 103, 105, 107, 106, 108, 110, 109]
        bars = make_bars(closes)
        tick = make_tick(bid=109.0)

        result = worker.compute(tick=tick, bar_history={'M5': bars}, current_bars={})

        expected_histogram = result.outputs['macd'] - result.outputs['signal']
        assert result.outputs['histogram'] == pytest.approx(expected_histogram, abs=0.0001)
