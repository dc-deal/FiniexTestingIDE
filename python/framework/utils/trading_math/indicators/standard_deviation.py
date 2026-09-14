"""
FiniexTestingIDE - Windowed Standard Deviation

Dispersion of the last `period` values around their own mean — the quantity
Bollinger bands are built from and the denominator the MA-trend line normalizes
its slope by.

Population standard deviation (ddof=0), which is what the charting convention uses
for Bollinger bands: the window IS the population being described, not a sample
drawn from a larger one.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd


def window_std(values: np.ndarray, period: int) -> float:
    """
    Population standard deviation of the last `period` values.

    Args:
        values: Value window, oldest first
        period: Number of trailing values to measure

    Returns:
        Standard deviation of the trailing window
    """
    return float(np.std(values[-period:]))


def window_std_series(values: pd.Series, period: int) -> pd.Series:
    """
    Rolling population standard deviation over `period` values.

    Args:
        values: Value series, oldest first
        period: Rolling window length

    Returns:
        Series of the same index, NaN until the window is full
    """
    return values.rolling(window=period).std(ddof=0)
