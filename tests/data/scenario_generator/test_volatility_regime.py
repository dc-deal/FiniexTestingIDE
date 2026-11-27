"""
A generated profile measures its periods against its own range (#561).

The volatility profile measures every period against a reference the profile chooses — since #647
its ATR over close against the trailing year. A generated profile re-bases the periods of its range
on their own absolute ATR, so what it carries depends on that range alone: neither on a decade of a
venue's history before it, nor on the reference the volatility profile measures against.
"""

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np

from python.framework.discoveries.volatility_profile_analyzer.volatility_regime import (
    classify_volatility_regime,
    measured_against_themselves,
)
from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityPeriod,
    VolatilityRegime,
)

_THRESHOLDS = [0.5, 0.8, 1.2, 1.8]


def _periods(*absolute: float, ratio: float = 7.0) -> List[VolatilityPeriod]:
    """
    Periods with the given absolute ATRs, one hour apart.

    Args:
        absolute: Each period's mean ATR in price units
        ratio: The ratio the volatility profile measured, the same for all — a re-base ignores it

    Returns:
        The periods
    """
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return [VolatilityPeriod(
        start_time=start + timedelta(hours=i), end_time=start + timedelta(hours=i + 1),
        session=TradingSession.LONDON, atr=ratio, atr_absolute=value, atr_percentile=0.0,
        regime=VolatilityRegime.MEDIUM, tick_count=10, tick_density=10.0, activity=1.0,
        bar_count=12, high=1.0, low=1.0, range_pips=0.0) for i, value in enumerate(absolute)]


def _former_rebase(absolute: np.ndarray, global_mean: float) -> List[tuple]:
    """
    What a generated profile carried before #647: each period's ratio to the whole history's mean
    ATR, divided by the mean of those ratios — as the profile writes it, rounded to 6 decimals.

    Args:
        absolute: The periods' absolute ATRs
        global_mean: The whole history's mean ATR the ratios were taken against

    Returns:
        One (atr, percentile, regime) per period
    """
    ratios = absolute / global_mean
    rebased = ratios / float(ratios.mean())
    return [(round(float(value), 6), float((rebased < value).sum() / len(rebased) * 100),
             classify_volatility_regime(float(value), _THRESHOLDS)) for value in rebased]


class TestTheRegimeOfARange:

    def test_a_range_far_above_the_whole_history_is_not_all_very_high(self):
        # Against a decade's mean these read 9-11× average; against themselves they are ordinary
        rebased = measured_against_themselves(_periods(9.0, 10.0, 11.0), _THRESHOLDS)
        assert [period.regime for period in rebased] == [
            VolatilityRegime.MEDIUM, VolatilityRegime.MEDIUM, VolatilityRegime.MEDIUM]
        assert [round(period.atr, 2) for period in rebased] == [0.9, 1.0, 1.1]

    def test_the_order_of_the_periods_is_kept(self):
        # Split points compare periods with each other, so re-basing cannot move one
        values = [3.0, 1.0, 2.0, 6.0]
        rebased = measured_against_themselves(_periods(*values), _THRESHOLDS)
        order = sorted(range(len(values)), key=lambda i: values[i])
        assert sorted(range(len(rebased)), key=lambda i: rebased[i].atr) == order
        assert [p.atr_percentile for p in rebased] == [50.0, 0.0, 25.0, 75.0]

    def test_periods_without_atr_are_left_as_they_are(self):
        periods = _periods(0.0, 0.0)
        assert measured_against_themselves(periods, _THRESHOLDS) is periods

    def test_the_boundaries_are_the_configured_ones(self):
        assert classify_volatility_regime(0.49, _THRESHOLDS) is VolatilityRegime.VERY_LOW
        assert classify_volatility_regime(1.0, _THRESHOLDS) is VolatilityRegime.MEDIUM
        assert classify_volatility_regime(1.8, _THRESHOLDS) is VolatilityRegime.VERY_HIGH


class TestAGeneratedProfileIsUnchangedByTheNewReference:
    """
    The volatility profile's reference moved from the whole history's mean ATR to the trailing
    year's ATR over close (#647). A generated profile re-bases on the absolute ATR, so its windows
    carry exactly what they carried before — the committed profiles stay valid.
    """

    def test_atr_percentile_and_regime_match_the_former_rebase_as_written(self):
        rng = np.random.default_rng(647)
        for _ in range(100):
            # Rounded, so that ties occur; a global mean far from the range's own
            absolute = np.round(rng.lognormal(0, 0.8, int(rng.integers(2, 400))), 4) + 0.0001
            global_mean = float(rng.uniform(0.01, 50.0))
            rebased = measured_against_themselves(
                _periods(*absolute, ratio=float(rng.uniform(0.1, 3.0))), _THRESHOLDS)

            assert [(round(p.atr, 6), p.atr_percentile, p.regime) for p in rebased] == (
                _former_rebase(absolute, global_mean))
