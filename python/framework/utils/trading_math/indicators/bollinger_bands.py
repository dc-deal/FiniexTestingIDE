"""
FiniexTestingIDE - Bollinger Bands

A moving-average midline with a band drawn a chosen number of standard deviations
either side of it. The midline's average is the caller's choice — SMA is Bollinger's
own and the usual default, EMA a common variant — which is why it arrives as MaType
rather than being decided here.

What this unit does NOT do is place a price inside the bands. Expressing a value as
a position between two bounds is normalization and belongs to the Normalizer (§38),
which is also where the clamped and unclamped readings are kept apart.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd

from python.framework.types.indicator_types import BollingerBandValues, MaType
from python.framework.utils.trading_math.indicators.moving_average import (
    moving_average,
    moving_average_series,
)
from python.framework.utils.trading_math.indicators.standard_deviation import (
    window_std,
    window_std_series,
)


def bollinger_bands(
    closes: np.ndarray,
    period: int,
    deviation: float,
    ma_type: MaType = MaType.SMA,
) -> BollingerBandValues:
    """
    The three band lines at the newest close.

    The dispersion is always measured over the trailing `period` closes; the
    midline may look further back when its average is recursive, which is what
    `moving_average_warmup_bars` sizes the window for.

    Args:
        closes: Close window, oldest first
        period: Band period
        deviation: Number of standard deviations between midline and each band
        ma_type: Which average forms the midline

    Returns:
        Upper, middle and lower line plus the standard deviation behind them
    """
    middle = moving_average(closes, period, ma_type)
    std_dev = window_std(closes, period)

    band_half = std_dev * deviation

    return BollingerBandValues(
        upper=middle + band_half,
        middle=middle,
        lower=middle - band_half,
        std_dev=std_dev,
    )


def bollinger_bands_series(
    closes: pd.Series,
    period: int,
    deviation: float,
    ma_type: MaType = MaType.SMA,
) -> pd.DataFrame:
    """
    The three band lines for every row.

    Args:
        closes: Close series, oldest first
        period: Band period
        deviation: Number of standard deviations between midline and each band
        ma_type: Which average forms the midline

    Returns:
        Frame of the same index with upper, middle, lower and std_dev columns
    """
    middle = moving_average_series(closes, period, ma_type)
    std_dev = window_std_series(closes, period)

    band_half = std_dev * deviation

    return pd.DataFrame({
        'upper': middle + band_half,
        'middle': middle,
        'lower': middle - band_half,
        'std_dev': std_dev,
    })
