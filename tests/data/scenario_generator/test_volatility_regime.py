"""
A generated profile measures its periods against its own range (#561).

The volatility profile measures every period against the symbol's whole history; once a venue's
published history precedes our collection, that history spans prices two orders of magnitude
apart, and a profile of half a year would be labelled by a decade it does not cover.
"""

from datetime import datetime, timedelta, timezone

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


def _periods(*ratios: float) -> list:
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return [VolatilityPeriod(
        start_time=start + timedelta(hours=i), end_time=start + timedelta(hours=i + 1),
        session=TradingSession.LONDON, atr=ratio, atr_percentile=0.0,
        regime=VolatilityRegime.MEDIUM, tick_count=10, tick_density=10.0, activity=1.0,
        bar_count=12, high=1.0, low=1.0, range_pips=0.0) for i, ratio in enumerate(ratios)]


class TestTheRegimeOfARange:

    def test_a_range_far_above_the_whole_history_is_not_all_very_high(self):
        # Against a decade's mean these read 9-11× average; against themselves they are ordinary
        rebased = measured_against_themselves(_periods(9.0, 10.0, 11.0), _THRESHOLDS)
        assert [period.regime for period in rebased] == [
            VolatilityRegime.MEDIUM, VolatilityRegime.MEDIUM, VolatilityRegime.MEDIUM]
        assert [round(period.atr, 2) for period in rebased] == [0.9, 1.0, 1.1]

    def test_the_order_of_the_periods_is_kept(self):
        # Split points compare periods with each other, so re-basing cannot move one
        ratios = [3.0, 1.0, 2.0, 6.0]
        rebased = measured_against_themselves(_periods(*ratios), _THRESHOLDS)
        order = sorted(range(len(ratios)), key=lambda i: ratios[i])
        assert sorted(range(len(rebased)), key=lambda i: rebased[i].atr) == order
        assert [p.atr_percentile for p in rebased] == [50.0, 0.0, 25.0, 75.0]

    def test_periods_without_atr_are_left_as_they_are(self):
        periods = _periods(0.0, 0.0)
        assert measured_against_themselves(periods, _THRESHOLDS) is periods

    def test_the_boundaries_are_the_configured_ones(self):
        assert classify_volatility_regime(0.49, _THRESHOLDS) is VolatilityRegime.VERY_LOW
        assert classify_volatility_regime(1.0, _THRESHOLDS) is VolatilityRegime.MEDIUM
        assert classify_volatility_regime(1.8, _THRESHOLDS) is VolatilityRegime.VERY_HIGH
