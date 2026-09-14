"""
FiniexTestingIDE - Moving Average Selection

Picks one of the three named averages by MaType. An indicator that offers its
caller a choice of smoothing — Bollinger's midline, the MA-trend line — asks here
instead of branching for itself, which is the same service pandas-ta's `ta.ma`
provides and the reason two workers no longer hold the same four-line if.

The warmup helper travels with it on purpose: how much history an average needs is
a property of the average, and a caller that picks the type without also picking
the window is exactly how an EMA silently degenerates into an SMA.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd

from python.framework.types.indicator_types import MaType
from python.framework.utils.trading_math.indicators.exponential_moving_average import (
    ema,
    ema_series,
    ema_warmup_bars,
)
from python.framework.utils.trading_math.indicators.simple_moving_average import (
    sma,
    sma_series,
)
from python.framework.utils.trading_math.indicators.wilder_moving_average import (
    rma,
    rma_series,
    rma_warmup_bars,
)


def moving_average(values: np.ndarray, period: int, ma_type: MaType) -> float:
    """
    The selected moving average at the newest value.

    Args:
        values: Value window, oldest first
        period: Average period
        ma_type: Which of the three averages to apply

    Returns:
        The average at the newest value
    """
    if ma_type == MaType.EMA:
        return ema(values, period)
    if ma_type == MaType.RMA:
        return rma(values, period)
    return sma(values, period)


def moving_average_series(values: pd.Series, period: int, ma_type: MaType) -> pd.Series:
    """
    The selected moving average for every row.

    Args:
        values: Value series, oldest first
        period: Average period
        ma_type: Which of the three averages to apply

    Returns:
        Series of the same index
    """
    if ma_type == MaType.EMA:
        return ema_series(values, period)
    if ma_type == MaType.RMA:
        return rma_series(values, period)
    return sma_series(values, period)


def moving_average_warmup_bars(period: int, ma_type: MaType) -> int:
    """
    History the selected average needs before its result is free of its seed.

    The SMA has no seed and therefore needs exactly its period; the two recursive
    averages need a multiple of it.

    Args:
        period: Average period
        ma_type: Which of the three averages to apply

    Returns:
        Number of values the caller should supply
    """
    if ma_type == MaType.EMA:
        return ema_warmup_bars(period)
    if ma_type == MaType.RMA:
        return rma_warmup_bars(period)
    return period
