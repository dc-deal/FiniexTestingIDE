"""
FiniexTestingIDE - True Range

How far price actually travelled in one bar, gaps included. The bar's own high-low
span understates a move that opened away from the previous close, so the true range
is the widest of three distances: the bar's span, and the reach from the previous
close to each of the bar's extremes.

The first bar of a series has no previous close and therefore no gap to account
for — its true range is its span. That is the established treatment, and it is why
the series form leaves the first row as high-low rather than as NaN.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import pandas as pd


def true_range(high: float, low: float, previous_close: float) -> float:
    """
    True range of one bar against the close before it.

    Args:
        high: Bar high
        low: Bar low
        previous_close: Close of the preceding bar

    Returns:
        The widest of span, high-to-previous-close and low-to-previous-close
    """
    return max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close),
    )


def true_range_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
) -> pd.Series:
    """
    True range for every bar in a frame.

    Args:
        high: Bar highs, oldest first
        low: Bar lows, oldest first
        close: Bar closes, oldest first

    Returns:
        Series of the same index; the first row is the bar's own span
    """
    previous_close = close.shift(1)
    high_low = high - low
    high_close = (high - previous_close).abs()
    low_close = (low - previous_close).abs()

    return pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
