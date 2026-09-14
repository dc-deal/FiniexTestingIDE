"""
FiniexTestingIDE - On-Balance Volume

Running total of volume, added on an up-close and subtracted on a down-close; a
close equal to the one before it leaves the total alone. The idea is that volume
moves before price does, so a total that keeps rising while price does not is a
different market from one where both stall.

The total starts at zero at the beginning of the window rather than at some
inception, so the LEVEL means nothing on its own and only its direction over the
window is readable. That is deliberate and it is why the worker above reports a
trend rather than a value alone.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd


def obv(closes: np.ndarray, volumes: np.ndarray) -> float:
    """
    On-balance volume accumulated across the window, starting from zero.

    Args:
        closes: Close window, oldest first
        volumes: Bar volumes for the same window, oldest first

    Returns:
        The accumulated total at the newest bar
    """
    if len(closes) < 2:
        return 0.0

    total = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            total += volumes[i]
        elif closes[i] < closes[i - 1]:
            total -= volumes[i]

    return float(total)


def obv_series(closes: pd.Series, volumes: pd.Series) -> pd.Series:
    """
    On-balance volume at every row, starting from zero at the first.

    Args:
        closes: Close series, oldest first
        volumes: Volume series for the same index, oldest first

    Returns:
        Series of the same index, starting at zero
    """
    direction = np.sign(closes.diff()).fillna(0.0)
    return (direction * volumes).cumsum()
