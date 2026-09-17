"""
FiniexTestingIDE - MA Trend Worker Computation Tests

Tests the MaTrendWorker compute() method: trend direction from the
volatility-normalized midline slope, the neutral band, SMA vs EMA, and the
relative-volatility output.

Key implementation details (verified from source):
- window = moving_average_warmup_bars(period, ma_type) → the period for an SMA, a
  multiple of it for a recursive average, because an EMA over exactly its own period
  returns its SMA seed and the ma_type setting would stop meaning anything
- ma_value = moving_average(close_prices[-window:], period, ma_type)
- std_window = window_std(close_prices, period)  → population std (ddof=0), over the
  PERIOD even when the average reads further back
- slope = Normalizer.normalize(ma_value - ma_prev, std_window)  (needs window+1 closes)
- direction = up if slope > neutral_band, down if slope < -neutral_band, else neutral
- volatility_pct = Normalizer.normalize(std_window, ma_value)
"""

import pytest
from tests.framework.worker_tests.worker_computation_tests.conftest import make_bars, make_tick

from python.framework.types.worker_types import WorkerResult
from python.framework.workers.core.ma_trend_worker import MaTrendWorker

RISING_CLOSES_6 = [100, 101, 102, 103, 104, 105]
FALLING_CLOSES_6 = [105, 104, 103, 102, 101, 100]
FLAT_CLOSES_6 = [100.0] * 6

# An EMA needs three periods of history plus one bar for the shifted slope window, so the
# six-bar series above cannot exercise it. CURVED on purpose as well: at steady state both
# averages lag a constant slope by exactly (period-1)/2, so on a STRAIGHT ramp the EMA and
# the SMA are indistinguishable and an "ema differs from sma" test would pass for the wrong
# reason. Accelerating away from the start is what separates them.
RISING_CURVE_16 = [round(100 + 0.05 * i * i, 4) for i in range(16)]
FALLING_CURVE_16 = [round(100 - 0.05 * i * i, 4) for i in range(16)]


class TestMaTrendDirection:
    """Direction classification from the normalized slope."""

    def test_up_on_rising_closes(self, mock_logger):
        worker = MaTrendWorker(
            name='test_ma_trend',
            parameters={'periods': {'M5': 5}, 'ma_type': 'ema', 'neutral_band': 0.1},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CURVE_16)
        result = worker.compute(tick=make_tick(bid=111.25), bar_history={'M5': bars}, current_bars={})

        assert isinstance(result, WorkerResult)
        assert result.get_signal('direction') == 'up'
        assert result.get_signal('slope') > 0.0

    def test_down_on_falling_closes(self, mock_logger):
        worker = MaTrendWorker(
            name='test_ma_trend',
            parameters={'periods': {'M5': 5}, 'ma_type': 'ema', 'neutral_band': 0.1},
            logger=mock_logger,
        )
        bars = make_bars(FALLING_CURVE_16)
        result = worker.compute(tick=make_tick(bid=88.75), bar_history={'M5': bars}, current_bars={})

        assert result.get_signal('direction') == 'down'
        assert result.get_signal('slope') < 0.0

    def test_neutral_on_flat_closes(self, mock_logger):
        worker = MaTrendWorker(
            name='test_ma_trend',
            parameters={'periods': {'M5': 5}, 'ma_type': 'ema', 'neutral_band': 0.1},
            logger=mock_logger,
        )
        bars = make_bars(FLAT_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=100.0), bar_history={'M5': bars}, current_bars={})

        assert result.get_signal('direction') == 'neutral'
        assert result.get_signal('slope') == 0.0

    def test_neutral_band_suppresses_direction(self, mock_logger):
        """A wide neutral band classifies a real slope as NEUTRAL (stand aside)."""
        worker = MaTrendWorker(
            name='test_ma_trend',
            parameters={'periods': {'M5': 5}, 'ma_type': 'ema', 'neutral_band': 10.0},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CURVE_16)
        result = worker.compute(tick=make_tick(bid=111.25), bar_history={'M5': bars}, current_bars={})

        assert result.get_signal('slope') > 0.0  # slope exists
        assert result.get_signal('direction') == 'neutral'  # but below the band


class TestMaTrendSlopeAndVolatility:
    """Slope normalization and volatility_pct."""

    def test_slope_zero_without_extra_bar(self, mock_logger):
        """Fewer bars than the average's own window → no shifted window → slope is 0.0."""
        worker = MaTrendWorker(
            name='test_ma_trend',
            parameters={'periods': {'M5': 5}, 'ma_type': 'ema', 'neutral_band': 0.1},
            logger=mock_logger,
        )
        bars = make_bars([100, 101, 102, 103, 104])  # 5 bars, period 5
        result = worker.compute(tick=make_tick(bid=104.0), bar_history={'M5': bars}, current_bars={})

        assert result.get_signal('slope') == 0.0
        assert result.get_signal('direction') == 'neutral'

    def test_volatility_pct_matches_std_over_ma(self, mock_logger):
        """volatility_pct = std_window / ma_value."""
        import numpy as np
        worker = MaTrendWorker(
            name='test_ma_trend',
            parameters={'periods': {'M5': 5}, 'ma_type': 'sma', 'neutral_band': 0.1},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=105.0), bar_history={'M5': bars}, current_bars={})

        # SMA window = [101,102,103,104,105]
        window = np.array([101, 102, 103, 104, 105], dtype=float)
        expected = float(np.std(window) / np.mean(window))
        assert result.get_signal('volatility_pct') == pytest.approx(expected, abs=0.0001)

    def test_volatility_pct_zero_when_flat(self, mock_logger):
        worker = MaTrendWorker(
            name='test_ma_trend',
            parameters={'periods': {'M5': 5}, 'ma_type': 'ema', 'neutral_band': 0.1},
            logger=mock_logger,
        )
        bars = make_bars(FLAT_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=100.0), bar_history={'M5': bars}, current_bars={})

        assert result.get_signal('volatility_pct') == 0.0


class TestMaTrendMaType:
    """SMA vs EMA midline + output key-set."""

    def test_ema_ma_value_differs_from_sma_on_trend(self, mock_logger):
        sma_worker = MaTrendWorker(
            name='test_sma',
            parameters={'periods': {'M5': 5}, 'ma_type': 'sma', 'neutral_band': 0.1},
            logger=mock_logger,
        )
        ema_worker = MaTrendWorker(
            name='test_ema',
            parameters={'periods': {'M5': 5}, 'ma_type': 'ema', 'neutral_band': 0.1},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CURVE_16)
        tick = make_tick(bid=111.25)

        sma_ma = sma_worker.compute(tick=tick, bar_history={'M5': bars}, current_bars={}).get_signal('ma_value')
        ema_ma = ema_worker.compute(tick=tick, bar_history={'M5': bars}, current_bars={}).get_signal('ma_value')

        assert ema_ma != pytest.approx(sma_ma, abs=0.001)
        assert ema_ma > sma_ma  # rising series → EMA leans toward recent highs

    def test_output_keys(self, mock_logger):
        worker = MaTrendWorker(
            name='test_ma_trend',
            parameters={'periods': {'M5': 5}, 'ma_type': 'ema', 'neutral_band': 0.1},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=105.0), bar_history={'M5': bars}, current_bars={})

        expected_keys = {'direction', 'slope', 'ma_value', 'volatility_pct', 'bars_used'}
        assert set(result.outputs.keys()) == expected_keys
