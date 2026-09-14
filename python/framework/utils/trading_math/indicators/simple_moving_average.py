"""
FiniexTestingIDE - Simple Moving Average

The arithmetic mean over the last `period` values. The plainest of the three
averages, and the only one with no memory beyond its window: two SMAs over the
same window agree regardless of what came before them.

Two forms. The scalar one answers for the newest value and is what the tick loop
calls; the series one answers for every row and is what the analysis plane calls.
They are pinned against each other by test rather than by shared code, because a
per-tick path that builds a DataFrame and a full-history path that loops in Python
are each wrong on the other's side.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd


def sma(values: np.ndarray, period: int) -> float:
    """
    Arithmetic mean of the last `period` values.

    A window shorter than `period` is averaged whole rather than refused — the
    caller's warmup contract decides how much history exists, and an indicator
    that raises mid-tick would turn a thin warmup into a dead session.

    Args:
        values: Value window, oldest first
        period: Number of trailing values to average

    Returns:
        Mean of the trailing window
    """
    return float(np.mean(values[-period:]))


def sma_series(values: pd.Series, period: int) -> pd.Series:
    """
    Rolling arithmetic mean over `period` values.

    Rows before the window is full are NaN, which is what distinguishes "not yet
    computable" from a number.

    Args:
        values: Value series, oldest first
        period: Rolling window length

    Returns:
        Series of the same index, NaN until the window is full
    """
    return values.rolling(window=period).mean()
