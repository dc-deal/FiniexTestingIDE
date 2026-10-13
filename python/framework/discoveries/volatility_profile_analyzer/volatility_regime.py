"""
Volatility Regime — where a period's ATR stands against a reference, asked once.

A period's regime and percentile say how volatile it was against OTHER periods, so they depend on
which periods those are. The volatility profile measures every period against the symbol's whole
history; a generated profile measures the periods of its own range against that range — once a
venue's published history precedes our collection (#561), the whole history spans a price range of
two orders of magnitude, and a profile of half a year would otherwise be labelled by a decade it
does not cover.
"""

from dataclasses import replace
from typing import List

import numpy as np

from python.framework.types.market_types.market_volatility_profile_types import (
    VolatilityPeriod,
    VolatilityRegime,
)


def classify_volatility_regime(ratio: float, thresholds: List[float]) -> VolatilityRegime:
    """
    Classify a period by its ATR relative to the reference average.

    Args:
        ratio: The period's ATR divided by the reference average ATR
        thresholds: The four ratio boundaries, ascending (discoveries config)

    Returns:
        The regime
    """
    if ratio < thresholds[0]:
        return VolatilityRegime.VERY_LOW
    if ratio < thresholds[1]:
        return VolatilityRegime.LOW
    if ratio < thresholds[2]:
        return VolatilityRegime.MEDIUM
    if ratio < thresholds[3]:
        return VolatilityRegime.HIGH
    return VolatilityRegime.VERY_HIGH


def measured_against_themselves(
    periods: List[VolatilityPeriod],
    thresholds: List[float]
) -> List[VolatilityPeriod]:
    """
    The same periods with ATR ratio, percentile and regime measured against their own average.

    A period's `atr` is a ratio to the reference average, so dividing every ratio by their mean
    re-bases them on these periods alone. Split points are unaffected — they compare periods with
    each other — only the numbers and labels a window carries change.

    Args:
        periods: Volatility periods of one range
        thresholds: The four ratio boundaries, ascending (discoveries config)

    Returns:
        The re-based periods; unchanged where no period has a positive ATR
    """
    ratios = np.array([period.atr for period in periods], dtype=float)
    reference = float(ratios.mean()) if len(ratios) else 0.0
    if not np.isfinite(reference) or reference <= 0.0:
        return periods
    rebased = ratios / reference
    return [
        replace(period,
                atr=float(ratio),
                atr_percentile=float((rebased < ratio).sum() / len(rebased) * 100),
                regime=classify_volatility_regime(float(ratio), thresholds))
        for period, ratio in zip(periods, rebased)]
