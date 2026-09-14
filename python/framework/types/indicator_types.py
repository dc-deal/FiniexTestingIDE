"""
FiniexTestingIDE - Indicator Types

The vocabulary the indicator library speaks: which moving average an indicator
smooths with, and the multi-field results some of them return.

MaType exists because the three averages are three different measures with three
established names, not variants of one — the same distinction pandas-ta draws with
`mamode` and TradingView with ta.sma / ta.ema / ta.rma. Naming them separately is
what keeps a smoothing choice a declared decision instead of an accident of
whichever implementation a call site happened to reach.
"""

from dataclasses import dataclass
from enum import Enum


class MaType(str, Enum):
    """
    Moving-average method an indicator smooths with.

    SMA: arithmetic mean over the window.
    EMA: alpha = 2/(period+1), seeded with the SMA of the first `period` values.
    RMA: alpha = 1/period — Wilder's smoothing, the basis of the standard ATR and RSI.
    """

    SMA = 'sma'
    EMA = 'ema'
    RMA = 'rma'


@dataclass(frozen=True)
class RsiValues:
    """
    RSI plus the two averages it is built from.

    Args:
        value: The oscillator value, 0-100
        avg_gain: Smoothed average gain over the period
        avg_loss: Smoothed average loss over the period
    """

    value: float
    avg_gain: float
    avg_loss: float


@dataclass(frozen=True)
class BollingerBandValues:
    """
    The three Bollinger lines plus the dispersion they were built from.

    Args:
        upper: Midline plus deviation * standard deviation
        middle: The moving-average midline
        lower: Midline minus deviation * standard deviation
        std_dev: Standard deviation over the window
    """

    upper: float
    middle: float
    lower: float
    std_dev: float


@dataclass(frozen=True)
class MacdValues:
    """
    MACD line, its signal line, and the components both are derived from.

    Args:
        macd: Fast EMA minus slow EMA
        signal: EMA of the MACD line over the signal period
        histogram: MACD line minus signal line
        fast_ema: The fast exponential moving average
        slow_ema: The slow exponential moving average
    """

    macd: float
    signal: float
    histogram: float
    fast_ema: float
    slow_ema: float
