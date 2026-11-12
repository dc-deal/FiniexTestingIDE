"""
FiniexTestingIDE - Tide and Wave Worker Computation Tests
The wave is the library's RSI over the first timeframe's closes, the tide the library's MACD
histogram and its change over the second timeframe's — each screen reads only its own bars, and a
configuration that does not give it two timeframes, the tide's the longer, is refused.
"""

import numpy as np
import pytest

from python.framework.utils.trading_math.indicators.exponential_moving_average import (
    ema_warmup_bars,
)
from python.framework.utils.trading_math.indicators.macd import macd
from python.framework.utils.trading_math.indicators.rsi import rsi
from python.framework.utils.trading_math.indicators.wilder_moving_average import (
    rma_warmup_bars,
)
from python.framework.workers.core.tide_and_wave_worker import TideAndWaveWorker
from tests.framework.worker_tests.worker_computation_tests.conftest import make_bars, make_tick

_PARAMETERS = {'periods': {'M5': 3, 'M30': 5}, 'fast_period': 3, 'slow_period': 5,
               'signal_period': 2}


def _worker(mock_logger) -> TideAndWaveWorker:
    return TideAndWaveWorker(name='tide_wave', parameters=dict(_PARAMETERS), logger=mock_logger)


def _closes(count: int, start: float, step: float) -> list:
    """A zigzag that rises on the whole, so neither screen sits on a flat value."""
    return [start + step * index + (0.4 if index % 3 == 0 else -0.3) for index in range(count)]


class TestEachScreenReadsItsOwnTimeframe:

    def test_the_wave_is_the_rsi_of_the_first_timeframe(self, mock_logger):
        worker = _worker(mock_logger)
        wave_closes = _closes(40, 100.0, 0.2)
        tide_closes = _closes(40, 200.0, -0.5)
        result = worker.compute(
            tick=make_tick(bid=wave_closes[-1]), current_bars={},
            bar_history={'M5': make_bars(wave_closes, 'M5'),
                         'M30': make_bars(tide_closes, 'M30')})

        window = rma_warmup_bars(3) + 1
        assert result.outputs['wave_rsi'] == pytest.approx(
            rsi(np.array(wave_closes[-window:]), 3).value)

    def test_the_tide_is_the_macd_histogram_of_the_second_and_its_change(self, mock_logger):
        worker = _worker(mock_logger)
        wave_closes = _closes(40, 100.0, 0.2)
        tide_closes = _closes(40, 200.0, -0.5)
        result = worker.compute(
            tick=make_tick(bid=wave_closes[-1]), current_bars={},
            bar_history={'M5': make_bars(wave_closes, 'M5'),
                         'M30': make_bars(tide_closes, 'M30')})

        window = np.array(tide_closes[-(ema_warmup_bars(5) + ema_warmup_bars(2) + 1):])
        now = macd(window, 3, 5, 2).histogram
        before = macd(window[:-1], 3, 5, 2).histogram
        assert result.outputs['tide_histogram'] == pytest.approx(now)
        assert result.outputs['tide_slope'] == pytest.approx(now - before)

    def test_the_wave_does_not_move_with_the_tides_bars(self, mock_logger):
        worker = _worker(mock_logger)
        wave_bars = make_bars(_closes(40, 100.0, 0.2), 'M5')
        first = worker.compute(tick=make_tick(bid=100.0), current_bars={}, bar_history={
            'M5': wave_bars, 'M30': make_bars(_closes(40, 200.0, -0.5), 'M30')})
        # A curved tide, not a straight one: the histogram cancels a constant trend
        curved = [150.0 + 0.05 * index * index for index in range(40)]
        second = worker.compute(tick=make_tick(bid=100.0), current_bars={}, bar_history={
            'M5': wave_bars, 'M30': make_bars(curved, 'M30')})

        assert first.outputs['wave_rsi'] == second.outputs['wave_rsi']
        assert first.outputs['tide_histogram'] != second.outputs['tide_histogram']


class TestTheConfiguration:

    def test_each_timeframe_warms_up_for_its_own_screen(self, mock_logger):
        assert _worker(mock_logger).get_warmup_requirements() == {
            'M5': rma_warmup_bars(3) + 1,
            'M30': ema_warmup_bars(5) + ema_warmup_bars(2) + 1}

    def test_one_timeframe_is_refused(self):
        with pytest.raises(ValueError, match='two timeframes'):
            TideAndWaveWorker.validate_config({'periods': {'M5': 14}})

    def test_a_tide_no_longer_than_the_wave_is_refused(self):
        with pytest.raises(ValueError, match='must be longer'):
            TideAndWaveWorker.validate_config({'periods': {'M30': 14, 'M5': 26}})

    def test_the_wave_first_and_the_longer_tide_second_is_accepted(self):
        TideAndWaveWorker.validate_config({'periods': {'M5': 14, 'M30': 26}})

    def test_the_tide_draws_on_the_second_timeframe(self):
        positions = {series.title: series.timeframe_position
                     for series in TideAndWaveWorker.get_indicator_series()}
        assert positions == {'Wave': 0, 'Tide': 1}
