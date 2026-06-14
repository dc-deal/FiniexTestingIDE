import numpy as np


def moving_average(closes: np.ndarray, period: int, ma_type: str = 'sma') -> float:
    """
    Moving average over a window (SMA or EMA).

    Args:
        closes: Close prices for the window
        period: Window length (drives the EMA smoothing factor)
        ma_type: 'sma' (arithmetic mean) or 'ema' (alpha=2/(period+1), seeded with first close)

    Returns:
        SMA or EMA of the window
    """
    if ma_type == 'ema':
        alpha = 2.0 / (period + 1)
        ema = float(closes[0])
        for price in closes[1:]:
            ema = alpha * float(price) + (1.0 - alpha) * ema
        return ema
    return float(np.mean(closes))
