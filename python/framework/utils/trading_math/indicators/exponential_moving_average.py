"""
FiniexTestingIDE - Exponential Moving Average

alpha = 2/(period+1), seeded with the SMA of the first `period` values. That seed
is the established one — TA-Lib, TradingView's ta.ema and pandas-ta all use it —
and it is the reason an EMA needs MORE history than its period: unlike the SMA,
the value depends on everything before it, so a window of exactly `period` values
has nothing left to recurse over and the EMA collapses into the SMA it was seeded
with. `ema_warmup_bars` is the answer to that, and callers are expected to ask it
rather than to pass their period straight through.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd

from python.framework.utils.trading_math.indicators.recursive_average import (
    recursive_average,
)

# How many periods of history an EMA needs before its seed stops showing. Measured
# 2026-09-14 over periods 2-50: three periods leave the seed holding under 1.9 % of
# its own weight. RMA_WARMUP_FACTOR is picked to land on that same residual, so the
# two averages are warmed to one standard rather than to two.
EMA_WARMUP_FACTOR = 3


def ema_warmup_bars(period: int) -> int:
    """
    History an EMA needs for its seed to have decayed out of the result.

    Args:
        period: The EMA period

    Returns:
        Number of values the caller should supply
    """
    return period * EMA_WARMUP_FACTOR


def ema(values: np.ndarray, period: int) -> float:
    """
    Exponential moving average of the whole window, seeded on its first `period` values.

    Consumes ALL of `values` — the window is the history, not the period. Too
    short a window to seed falls back to the plain mean rather than raising: the
    caller's warmup contract decides how much history exists, and an indicator
    that raises mid-tick would turn a thin warmup into a dead session.

    Args:
        values: Value window, oldest first
        period: EMA period, driving both the smoothing factor and the seed length

    Returns:
        The exponential moving average at the newest value
    """
    return recursive_average(values, period, 2.0 / (period + 1))


def ema_series(values: pd.Series, period: int) -> pd.Series:
    """
    Exponential moving average for every row, seeded on the first `period` values.

    Rows before the seed is available are NaN, which is what distinguishes "not
    yet computable" from a number.

    Args:
        values: Value series, oldest first
        period: EMA period

    Returns:
        Series of the same index, NaN until the seed row
    """
    if len(values) < period:
        return pd.Series(np.nan, index=values.index, dtype=float)

    # The seed replaces the row it sits on and the leading rows drop out, so the
    # recursion below starts exactly where the scalar form starts.
    seeded = values.astype(float).copy()
    seeded.iloc[:period - 1] = np.nan
    seeded.iloc[period - 1] = float(values.iloc[:period].mean())

    return seeded.ewm(span=period, adjust=False).mean()
