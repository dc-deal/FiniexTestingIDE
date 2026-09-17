"""
FiniexTestingIDE - Wilder's Moving Average (RMA)

alpha = 1/period, seeded with the SMA of the first `period` values. Welles Wilder
introduced it in 1978 together with ATR and RSI, and it is what both of those mean
by "average" — TA-Lib, TradingView's ta.rma and pandas-ta's mamode='rma' all agree.
It is deliberately NOT an EMA of the same period: at period 14 its smoothing factor
is 0.071 against the EMA's 0.133, so it reacts about half as fast.

Like the EMA it depends on everything before it, and it decays more slowly, so it
needs more history: `rma_warmup_bars` asks for five periods where the EMA asks for
three, which is what puts both averages on the same residual rather than on the
same period count.

A window of exactly `period` values collapses the RMA into the SMA it was seeded
with — which is precisely how the project's RSI became Cutler's RSI without anyone
choosing that.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd

from python.framework.utils.trading_math.indicators.recursive_average import (
    recursive_average,
)

# Measured 2026-09-14 over periods 2-50: five periods leave the seed holding under
# 1.8 % of its own weight — the same residual EMA_WARMUP_FACTOR buys in three.
RMA_WARMUP_FACTOR = 5


def rma_warmup_bars(period: int) -> int:
    """
    History an RMA needs for its seed to have decayed out of the result.

    Args:
        period: The RMA period

    Returns:
        Number of values the caller should supply
    """
    return period * RMA_WARMUP_FACTOR


def rma(values: np.ndarray, period: int) -> float:
    """
    Wilder's moving average of the whole window, seeded on its first `period` values.

    Consumes ALL of `values` — the window is the history, not the period. Too
    short a window to seed falls back to the plain mean rather than raising, which
    is also the value Wilder's own seed would produce at that length.

    Args:
        values: Value window, oldest first
        period: RMA period, driving both the smoothing factor and the seed length

    Returns:
        Wilder's moving average at the newest value
    """
    return recursive_average(values, period, 1.0 / period)


def rma_series(values: pd.Series, period: int) -> pd.Series:
    """
    Wilder's moving average for every row, seeded on the first `period` values.

    Rows before the seed is available are NaN, which is what distinguishes "not
    yet computable" from a number.

    Args:
        values: Value series, oldest first
        period: RMA period

    Returns:
        Series of the same index, NaN until the seed row
    """
    if len(values) < period:
        return pd.Series(np.nan, index=values.index, dtype=float)

    seeded = values.astype(float).copy()
    seeded.iloc[:period - 1] = np.nan
    seeded.iloc[period - 1] = float(values.iloc[:period].mean())

    return seeded.ewm(alpha=1.0 / period, adjust=False).mean()
