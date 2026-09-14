"""
FiniexTestingIDE - Relative Strength Index

Wilder's oscillator: average gain against average loss over the period, expressed
as 0-100. The averaging method is what the name settles — Wilder smoothed both
sides with his own RMA, and that is what TA-Lib, TradingView and every charting
package return for "RSI".

Averaging the two sides with a plain arithmetic mean instead produces a different,
also-named indicator: Cutler's RSI, which reacts faster and is not what a reader
comparing against a chart expects. This project computed Cutler's under the plain
name until 2026-09-14. The variant stays reachable through `smoothing`; the default
is the standard.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd

from python.framework.types.indicator_types import MaType, RsiValues
from python.framework.utils.trading_math.indicators.moving_average import (
    moving_average,
    moving_average_series,
)

# An RSI with no price movement to measure has no defined strength ratio. Neutral
# is the honest answer, and it is unreachable through a worker, whose warmup
# contract guarantees a window.
NEUTRAL_RSI = 50.0


def rsi(
    closes: np.ndarray,
    period: int,
    smoothing: MaType = MaType.RMA,
) -> RsiValues:
    """
    Relative strength index at the newest close.

    Consumes the whole window — Wilder's smoothing is recursive, so the window is
    the history rather than the period, and a window of exactly `period` deltas
    collapses the RMA into the simple mean that makes this Cutler's RSI.

    Args:
        closes: Close window, oldest first
        period: RSI period, counted in price deltas
        smoothing: Which average smooths gains and losses; Wilder by definition

    Returns:
        The oscillator value with the two averages behind it
    """
    deltas = np.diff(closes)
    if len(deltas) == 0:
        return RsiValues(value=NEUTRAL_RSI, avg_gain=0.0, avg_loss=0.0)

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = moving_average(gains, period, smoothing)
    avg_loss = moving_average(losses, period, smoothing)

    if avg_loss == 0:
        value = 100.0
    else:
        rs = avg_gain / avg_loss
        value = 100.0 - (100.0 / (1.0 + rs))

    return RsiValues(value=float(value), avg_gain=avg_gain, avg_loss=avg_loss)


def rsi_series(
    closes: pd.Series,
    period: int,
    smoothing: MaType = MaType.RMA,
) -> pd.Series:
    """
    Relative strength index for every row.

    Args:
        closes: Close series, oldest first
        period: RSI period, counted in price deltas
        smoothing: Which average smooths gains and losses; Wilder by definition

    Returns:
        Series of the same index, NaN until the smoothing has its seed
    """
    deltas = closes.diff()
    gains = deltas.clip(lower=0.0)
    losses = (-deltas).clip(lower=0.0)

    # The first row carries no delta and must not enter the seed, which is what
    # dropping it here and reindexing afterwards achieves.
    avg_gain = moving_average_series(gains.iloc[1:], period, smoothing)
    avg_loss = moving_average_series(losses.iloc[1:], period, smoothing)

    relative_strength = avg_gain / avg_loss
    values = 100.0 - (100.0 / (1.0 + relative_strength))
    values = values.where(avg_loss != 0, 100.0)

    return values.reindex(closes.index)
