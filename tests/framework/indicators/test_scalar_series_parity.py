"""
The promise that makes two forms of one indicator still one definition.

Every indicator here exists twice: a scalar form the tick loop calls with a window of bars,
and a series form the analysis plane calls with a frame of months. That is not a convenience
— forcing either onto the other puts pandas inside the per-tick path (§17, where worker and
decision work is already ~79 % of tick time) or a Python loop across 300 000 rows.

Two implementations of one definition are only one definition if something measures that
they agree, and nothing else in the suite does: the worker tests exercise the scalar form
and the discovery code the series form, so each is checked against itself. This file is the
only place the two meet.

Agreement is to floating-point tolerance, not bit-for-bit. The two forms accumulate in a
different order — a Python recursion against pandas' own — and demanding identical last bits
would pin the accumulation order rather than the definition.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.types.indicator_types import MaType
from python.framework.utils.trading_math.indicators.atr import atr, atr_series
from python.framework.utils.trading_math.indicators.bollinger_bands import (
    bollinger_bands,
    bollinger_bands_series,
)
from python.framework.utils.trading_math.indicators.macd import macd, macd_series
from python.framework.utils.trading_math.indicators.moving_average import (
    moving_average,
    moving_average_series,
)
from python.framework.utils.trading_math.indicators.on_balance_volume import (
    obv,
    obv_series,
)
from python.framework.utils.trading_math.indicators.rsi import rsi, rsi_series
from python.framework.utils.trading_math.indicators.standard_deviation import (
    window_std,
    window_std_series,
)

# Long enough that every recursive average is past its warmup at the newest row, which is
# the only row the two forms can be compared on.
_BARS = 400

_PERIODS = [2, 5, 14, 20, 50]
_MA_TYPES = [MaType.SMA, MaType.EMA, MaType.RMA]


@pytest.fixture(scope='module')
def market():
    """One synthetic instrument, built once — the frame is read, never mutated."""
    rng = np.random.default_rng(101)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.6, _BARS))
    return pd.DataFrame({
        'high': closes + np.abs(rng.normal(0, 0.4, _BARS)),
        'low': closes - np.abs(rng.normal(0, 0.4, _BARS)),
        'close': closes,
        'volume': rng.integers(1, 500, _BARS).astype(float),
    })


class TestTheTwoFormsAgreeAtTheNewestValue:

    @pytest.mark.parametrize('ma_type', _MA_TYPES)
    @pytest.mark.parametrize('period', _PERIODS)
    def test_moving_averages(self, market, ma_type, period):
        assert moving_average(market['close'].to_numpy(), period, ma_type) == (
            pytest.approx(moving_average_series(market['close'], period, ma_type).iloc[-1])
        )

    @pytest.mark.parametrize('period', _PERIODS)
    def test_standard_deviation(self, market, period):
        assert window_std(market['close'].to_numpy(), period) == pytest.approx(
            window_std_series(market['close'], period).iloc[-1]
        )

    @pytest.mark.parametrize('smoothing', _MA_TYPES)
    @pytest.mark.parametrize('period', _PERIODS)
    def test_atr(self, market, smoothing, period):
        scalar = atr(
            market['high'].to_numpy(), market['low'].to_numpy(),
            market['close'].to_numpy(), period, smoothing=smoothing,
        )
        series = atr_series(
            market['high'], market['low'], market['close'], period, smoothing=smoothing,
        ).iloc[-1]
        assert scalar == pytest.approx(series)

    @pytest.mark.parametrize('smoothing', _MA_TYPES)
    @pytest.mark.parametrize('period', _PERIODS)
    def test_rsi(self, market, smoothing, period):
        assert rsi(market['close'].to_numpy(), period, smoothing=smoothing).value == (
            pytest.approx(rsi_series(market['close'], period, smoothing=smoothing).iloc[-1])
        )

    @pytest.mark.parametrize('ma_type', _MA_TYPES)
    @pytest.mark.parametrize('deviation', [1.0, 2.0, 2.5])
    @pytest.mark.parametrize('period', _PERIODS)
    def test_bollinger_bands(self, market, ma_type, deviation, period):
        scalar = bollinger_bands(
            market['close'].to_numpy(), period, deviation, ma_type=ma_type
        )
        series = bollinger_bands_series(
            market['close'], period, deviation, ma_type=ma_type
        ).iloc[-1]

        assert scalar.upper == pytest.approx(series['upper'])
        assert scalar.middle == pytest.approx(series['middle'])
        assert scalar.lower == pytest.approx(series['lower'])
        assert scalar.std_dev == pytest.approx(series['std_dev'])

    @pytest.mark.parametrize('fast, slow, signal', [
        (12, 26, 9),      # the conventional setting, and the one every profile uses
        (5, 13, 4),       # faster, shorter warmup
        (20, 50, 15),     # slower, deep warmup
    ])
    def test_macd(self, market, fast, slow, signal):
        scalar = macd(market['close'].to_numpy(), fast, slow, signal)
        series = macd_series(market['close'], fast, slow, signal).iloc[-1]

        assert scalar.macd == pytest.approx(series['macd'])
        assert scalar.signal == pytest.approx(series['signal'])
        assert scalar.histogram == pytest.approx(series['histogram'])

    def test_on_balance_volume(self, market):
        assert obv(market['close'].to_numpy(), market['volume'].to_numpy()) == (
            pytest.approx(obv_series(market['close'], market['volume']).iloc[-1])
        )
