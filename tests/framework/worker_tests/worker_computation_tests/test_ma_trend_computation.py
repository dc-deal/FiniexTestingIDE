"""
FiniexTestingIDE - MA Trend Worker Computation Tests

Tests the MaTrendWorker compute() method: trend direction from the
volatility-normalized midline slope, the neutral band, SMA vs EMA, and the
relative-volatility output.

Key implementation details (verified from source):
- ma_value = moving_average(close_prices[-period:], period, ma_type)
- std_window = np.std(close_prices)  → population std (ddof=0)
- slope = Normalizer.normalize(ma_value - ma_prev, std_window)  (needs period+1 closes)
- direction = up if slope > neutral_band, down if slope < -neutral_band, else neutral
- volatility_pct = Normalizer.normalize(std_window, ma_value)
"""

import pytest

from python.framework.workers.core.ma_trend_worker import MaTrendWorker
from python.framework.types.worker_types import WorkerResult

from conftest import make_bars, make_tick


RISING_CLOSES_6 = [100, 101, 102, 103, 104, 105]
FALLING_CLOSES_6 = [105, 104, 103, 102, 101, 100]
FLAT_CLOSES_6 = [100.0] * 6


class TestMaTrendDirection:
    """Direction classification from the normalized slope."""

    def test_up_on_rising_closes(self, mock_logger):
        worker = MaTrendWorker(
            name="test_ma_trend",
            parameters={"periods": {"M5": 5}, "ma_type": "ema", "neutral_band": 0.1},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=105.0), bar_history={"M5": bars}, current_bars={})

        assert isinstance(result, WorkerResult)
        assert result.get_signal('direction') == 'up'
        assert result.get_signal('slope') > 0.0

    def test_down_on_falling_closes(self, mock_logger):
        worker = MaTrendWorker(
            name="test_ma_trend",
            parameters={"periods": {"M5": 5}, "ma_type": "ema", "neutral_band": 0.1},
            logger=mock_logger,
        )
        bars = make_bars(FALLING_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=100.0), bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('direction') == 'down'
        assert result.get_signal('slope') < 0.0

    def test_neutral_on_flat_closes(self, mock_logger):
        worker = MaTrendWorker(
            name="test_ma_trend",
            parameters={"periods": {"M5": 5}, "ma_type": "ema", "neutral_band": 0.1},
            logger=mock_logger,
        )
        bars = make_bars(FLAT_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=100.0), bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('direction') == 'neutral'
        assert result.get_signal('slope') == 0.0

    def test_neutral_band_suppresses_direction(self, mock_logger):
        """A wide neutral band classifies a real slope as NEUTRAL (stand aside)."""
        worker = MaTrendWorker(
            name="test_ma_trend",
            parameters={"periods": {"M5": 5}, "ma_type": "ema", "neutral_band": 10.0},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=105.0), bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('slope') > 0.0  # slope exists
        assert result.get_signal('direction') == 'neutral'  # but below the band


class TestMaTrendSlopeAndVolatility:
    """Slope normalization and volatility_pct."""

    def test_slope_zero_without_extra_bar(self, mock_logger):
        """Exactly `period` bars → no previous window → slope falls back to 0.0."""
        worker = MaTrendWorker(
            name="test_ma_trend",
            parameters={"periods": {"M5": 5}, "ma_type": "ema", "neutral_band": 0.1},
            logger=mock_logger,
        )
        bars = make_bars([100, 101, 102, 103, 104])  # 5 bars, period 5
        result = worker.compute(tick=make_tick(bid=104.0), bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('slope') == 0.0
        assert result.get_signal('direction') == 'neutral'

    def test_volatility_pct_matches_std_over_ma(self, mock_logger):
        """volatility_pct = std_window / ma_value."""
        import numpy as np
        worker = MaTrendWorker(
            name="test_ma_trend",
            parameters={"periods": {"M5": 5}, "ma_type": "sma", "neutral_band": 0.1},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=105.0), bar_history={"M5": bars}, current_bars={})

        # SMA window = [101,102,103,104,105]
        window = np.array([101, 102, 103, 104, 105], dtype=float)
        expected = float(np.std(window) / np.mean(window))
        assert result.get_signal('volatility_pct') == pytest.approx(expected, abs=0.0001)

    def test_volatility_pct_zero_when_flat(self, mock_logger):
        worker = MaTrendWorker(
            name="test_ma_trend",
            parameters={"periods": {"M5": 5}, "ma_type": "ema", "neutral_band": 0.1},
            logger=mock_logger,
        )
        bars = make_bars(FLAT_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=100.0), bar_history={"M5": bars}, current_bars={})

        assert result.get_signal('volatility_pct') == 0.0


class TestMaTrendMaType:
    """SMA vs EMA midline + output key-set."""

    def test_ema_ma_value_differs_from_sma_on_trend(self, mock_logger):
        sma_worker = MaTrendWorker(
            name="test_sma",
            parameters={"periods": {"M5": 5}, "ma_type": "sma", "neutral_band": 0.1},
            logger=mock_logger,
        )
        ema_worker = MaTrendWorker(
            name="test_ema",
            parameters={"periods": {"M5": 5}, "ma_type": "ema", "neutral_band": 0.1},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CLOSES_6)
        tick = make_tick(bid=105.0)

        sma_ma = sma_worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={}).get_signal('ma_value')
        ema_ma = ema_worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={}).get_signal('ma_value')

        assert ema_ma != pytest.approx(sma_ma, abs=0.001)
        assert ema_ma > sma_ma  # rising series → EMA leans toward recent highs

    def test_output_keys(self, mock_logger):
        worker = MaTrendWorker(
            name="test_ma_trend",
            parameters={"periods": {"M5": 5}, "ma_type": "ema", "neutral_band": 0.1},
            logger=mock_logger,
        )
        bars = make_bars(RISING_CLOSES_6)
        result = worker.compute(tick=make_tick(bid=105.0), bar_history={"M5": bars}, current_bars={})

        expected_keys = {'direction', 'slope', 'ma_value', 'volatility_pct', 'bars_used'}
        assert set(result.outputs.keys()) == expected_keys
