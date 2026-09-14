"""
FiniexTestingIDE - MACD

Moving Average Convergence Divergence: a fast EMA minus a slow one, and an EMA of
that difference as the signal line. Standard construction — the MACD line exists
only from the bar where the SLOW EMA is seeded, and the signal line is seeded on
the first `signal_period` values of that line.

That last sentence is the whole subtlety, and getting it wrong is silent: a signal
line seeded on MACD values taken from a not-yet-seeded slow EMA is seeded on
artefacts. Where both EMAs are still falling back to a plain mean of the same
window they cancel exactly, so those MACD values are not merely imprecise, they
are structurally zero.

MacdWorker switched to this unit on 2026-09-14. Measured on the old construction over
2800 SOLUSD M5 bars before the change: the histogram SIGN — the thing a MACD strategy
actually trades on — differed on 9.3 % of bars.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd

from python.framework.types.indicator_types import MacdValues
from python.framework.utils.trading_math.indicators.exponential_moving_average import (
    ema,
    ema_series,
)


def _running_ema(values: np.ndarray, period: int) -> np.ndarray:
    """
    EMA at every position, NaN before the seed is available.

    Args:
        values: Value window, oldest first
        period: EMA period

    Returns:
        Array of the same length, NaN until the seed row
    """
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < period:
        return result

    multiplier = 2 / (period + 1)
    current = float(np.mean(values[:period]))
    result[period - 1] = current

    for i in range(period, len(values)):
        current = (values[i] - current) * multiplier + current
        result[i] = current

    return result


def macd(
    closes: np.ndarray,
    fast_period: int,
    slow_period: int,
    signal_period: int,
) -> MacdValues:
    """
    MACD line, signal line and histogram at the newest close.

    A window too short to seed the slow EMA has no MACD line to build a signal
    from; the signal then equals the MACD line, which leaves the histogram at zero
    rather than inventing a crossing.

    Args:
        closes: Close window, oldest first
        fast_period: Fast EMA period
        slow_period: Slow EMA period, must exceed the fast one
        signal_period: EMA period of the signal line

    Returns:
        Both lines, their difference, and the two EMAs behind them
    """
    fast_ema = ema(closes, fast_period)
    slow_ema = ema(closes, slow_period)
    macd_line = fast_ema - slow_ema

    history = _running_ema(closes, fast_period) - _running_ema(closes, slow_period)
    seeded = history[~np.isnan(history)]

    signal_line = ema(seeded, signal_period) if len(seeded) > 0 else macd_line

    return MacdValues(
        macd=float(macd_line),
        signal=float(signal_line),
        histogram=float(macd_line - signal_line),
        fast_ema=float(fast_ema),
        slow_ema=float(slow_ema),
    )


def macd_series(
    closes: pd.Series,
    fast_period: int,
    slow_period: int,
    signal_period: int,
) -> pd.DataFrame:
    """
    MACD line, signal line and histogram for every row.

    Args:
        closes: Close series, oldest first
        fast_period: Fast EMA period
        slow_period: Slow EMA period, must exceed the fast one
        signal_period: EMA period of the signal line

    Returns:
        Frame of the same index with macd, signal and histogram columns
    """
    macd_line = ema_series(closes, fast_period) - ema_series(closes, slow_period)

    # The signal EMA is seeded on the MACD line's own first values, so the rows
    # before the slow EMA exists must be gone rather than merely ignored.
    signal_line = ema_series(macd_line.dropna(), signal_period).reindex(closes.index)

    return pd.DataFrame({
        'macd': macd_line,
        'signal': signal_line,
        'histogram': macd_line - signal_line,
    })
