"""
FiniexTestingIDE - Average True Range

Wilder's 1978 volatility measure: the true range, smoothed. The smoothing is the
part that carries the name — an ATR is an RMA of true range, and that is what
TA-Lib, TradingView and pandas-ta return for "ATR". An EMA of the same period is a
legitimate variant and reacts about twice as fast, so it stays reachable through
`smoothing`, but it is never what the bare name means.

This project computed the EMA variant under the plain name until 2026-09-14, which
made every ATR figure roughly a period-7.5 Wilder ATR wearing a period-14 label.
Nothing downstream decided on it at runtime (§38 keeps volatility profiles out of
the execution path), so the correction cost scenario-generation reruns rather than
run results.

This is part of a single-source module: never reimplement it, never bypass it.
"""

import numpy as np
import pandas as pd

from python.framework.types.indicator_types import MaType
from python.framework.utils.trading_math.indicators.moving_average import (
    moving_average,
    moving_average_series,
)
from python.framework.utils.trading_math.indicators.true_range import (
    true_range,
    true_range_series,
)


def atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int,
    smoothing: MaType = MaType.RMA,
) -> float:
    """
    Average true range at the newest bar.

    Consumes the whole window — the smoothing is recursive, so the window is the
    history rather than the period. `moving_average_warmup_bars` says how much of
    it the caller should supply.

    Args:
        highs: Bar highs, oldest first
        lows: Bar lows, oldest first
        closes: Bar closes, oldest first
        period: ATR period
        smoothing: Which average smooths the true range; Wilder by definition

    Returns:
        The average true range at the newest bar
    """
    if len(highs) == 0:
        return 0.0

    ranges = np.empty(len(highs), dtype=float)
    ranges[0] = highs[0] - lows[0]
    for i in range(1, len(highs)):
        ranges[i] = true_range(highs[i], lows[i], closes[i - 1])

    return moving_average(ranges, period, smoothing)


def atr_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int,
    smoothing: MaType = MaType.RMA,
) -> pd.Series:
    """
    Average true range for every bar in a frame.

    Args:
        high: Bar highs, oldest first
        low: Bar lows, oldest first
        close: Bar closes, oldest first
        period: ATR period
        smoothing: Which average smooths the true range; Wilder by definition

    Returns:
        Series of the same index, NaN until the smoothing has its seed
    """
    return moving_average_series(
        true_range_series(high, low, close), period, smoothing
    )
