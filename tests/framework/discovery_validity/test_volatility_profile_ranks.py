"""
The volatility profile ranks every period against all of them, in one pass.

Each period's `atr_percentile` is the share of periods whose ATR lies strictly below its own. It
used to be counted against every period, per period, which grew with the square of the history:
on a decade of hourly Kraken periods it took most of an import's cache step. One sort and a binary
search per value give the same count with the same arithmetic, so the ranks may not move by a bit.
A one-hour period takes its session from its hour, which every bar in it shares.

The frames are synthetic, in a temporary directory: no archive is read.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.discoveries.volatility_profile_analyzer.volatility_profile_analyzer import (
    VolatilityProfileAnalyzer,
)
from python.framework.utils.market_session_utils import get_session_from_utc_hour

_BARS = 1200


def _ranked_one_by_one(values: np.ndarray) -> np.ndarray:
    """The former definition: per value, the share of all values strictly below it."""
    return np.array([(values < value).sum() / len(values) * 100 for value in values])


@pytest.fixture
def analyzer(tmp_path, monkeypatch):
    """An analyzer pointed at a synthetic M5 series instead of the archive."""
    rng = np.random.default_rng(2611)
    closes = 50.0 + np.cumsum(rng.normal(0, 0.2, _BARS))
    frame = pd.DataFrame({
        'timestamp': pd.date_range('2026-03-02', periods=_BARS, freq='5min', tz='UTC'),
        'open': closes,
        'high': closes + np.abs(rng.normal(0, 0.1, _BARS)),
        'low': closes - np.abs(rng.normal(0, 0.1, _BARS)),
        'close': closes,
        'volume': rng.integers(1, 100, _BARS).astype(float),
        'tick_count': rng.integers(5, 60, _BARS).astype(int),
    })
    path = tmp_path / 'synthetic_M5.parquet'
    frame.to_parquet(path)
    instance = VolatilityProfileAnalyzer()
    monkeypatch.setattr(instance._bar_index, 'get_bar_file',
                        lambda broker_type, symbol, timeframe: str(path))
    return instance


class TestPercentileRanks:

    def test_each_rank_is_the_share_strictly_below(self):
        ranks = VolatilityProfileAnalyzer._get_percentiles(None, np.array([1.0, 2.0, 2.0, 3.0]))
        assert list(ranks) == [0.0, 25.0, 25.0, 75.0]

    def test_ranks_match_the_count_against_every_value_bit_for_bit(self):
        rng = np.random.default_rng(7)
        for _ in range(50):
            # Rounded, so that ties occur
            values = np.round(rng.lognormal(0, 1, int(rng.integers(1, 2000))), 3)
            expected = _ranked_one_by_one(values)
            ranks = VolatilityProfileAnalyzer._get_percentiles(None, values)
            assert ranks.dtype == expected.dtype
            assert np.array_equal(ranks, expected)

    def test_no_values_give_no_ranks(self):
        assert len(VolatilityProfileAnalyzer._get_percentiles(None, np.array([]))) == 0


class TestTheProfileItBuilds:

    def test_every_period_carries_the_rank_of_its_own_atr(self, analyzer):
        periods = analyzer.get_periods('kraken_spot', 'BTCUSD', 'M5')
        assert periods, 'the fixture must produce periods, or this proves nothing'
        ratios = np.array([period.atr for period in periods])
        ranks = np.array([period.atr_percentile for period in periods])
        # The ratio is the ATR divided by one common mean, so it ranks the periods as the ATR does
        assert np.array_equal(ranks, _ranked_one_by_one(ratios))

    def test_a_one_hour_period_takes_the_session_of_its_hour(self, analyzer):
        periods = analyzer.get_periods('kraken_spot', 'BTCUSD', 'M5')
        for period in periods:
            assert period.session.value == get_session_from_utc_hour(period.start_time.hour)
