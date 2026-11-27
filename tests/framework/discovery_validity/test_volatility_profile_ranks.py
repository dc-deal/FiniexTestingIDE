"""
The volatility profile measures each period against the year up to it, in one pass (#647).

Each period's `atr` is its ATR over close divided by the mean of the periods of the 365 days up to
and including it, and its `atr_percentile` the share of those periods strictly below it. Against
the whole history a venue's record that begins years before our collection labelled whole years
by other years' prices; over the bar's close and a trailing year, a price that grew a hundredfold
at steady relative volatility reads as ordinary throughout. Ranking each period against its year,
per period, would grow with the history times the year; one rolling pass gives the same count.
A one-hour period takes its session from its hour, which every bar in it shares.

The frames are synthetic, in a temporary directory: no archive is read.
"""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from python.framework.discoveries.volatility_profile_analyzer.volatility_profile_analyzer import (
    VolatilityProfileAnalyzer,
)
from python.framework.types.market_types.market_volatility_profile_types import VolatilityRegime
from python.framework.utils.market_session_utils import get_session_from_utc_hour

_BARS = 1200
_YEAR = timedelta(days=365)


def _ranked_one_by_one(starts: pd.DatetimeIndex, values: np.ndarray) -> np.ndarray:
    """Per value, the share of the values of its trailing year strictly below it — counted plainly."""
    ranks = []
    for start, value in zip(starts, values):
        year = values[(starts > start - _YEAR) & (starts <= start)]
        ranks.append((year < value).sum() / len(year) * 100)
    return np.array(ranks)


def _analyzer(tmp_path, monkeypatch, frame: pd.DataFrame) -> VolatilityProfileAnalyzer:
    """An analyzer pointed at a synthetic bar series instead of the archive."""
    path = tmp_path / 'synthetic.parquet'
    frame.to_parquet(path)
    instance = VolatilityProfileAnalyzer()
    monkeypatch.setattr(instance._bar_index, 'get_bar_file',
                        lambda broker_type, symbol, timeframe: str(path))
    return instance


@pytest.fixture
def analyzer(tmp_path, monkeypatch):
    """An analyzer over a synthetic M5 series of a hundred hours."""
    rng = np.random.default_rng(2611)
    closes = 50.0 + np.cumsum(rng.normal(0, 0.2, _BARS))
    return _analyzer(tmp_path, monkeypatch, pd.DataFrame({
        'timestamp': pd.date_range('2026-03-02', periods=_BARS, freq='5min', tz='UTC'),
        'open': closes,
        'high': closes + np.abs(rng.normal(0, 0.1, _BARS)),
        'low': closes - np.abs(rng.normal(0, 0.1, _BARS)),
        'close': closes,
        'volume': rng.integers(1, 100, _BARS).astype(float),
        'tick_count': rng.integers(5, 60, _BARS).astype(int),
    }))


class TestTheTrailingYear:

    def test_each_rank_is_the_share_of_its_year_strictly_below(self):
        starts = pd.date_range('2026-01-01', periods=4, freq='h', tz='UTC')
        _, ranks = VolatilityProfileAnalyzer._trailing_year_reference(
            list(starts), np.array([2.0, 1.0, 2.0, 3.0]))
        # Each against the periods up to and including it
        assert list(ranks) == [0.0, 0.0, 1 / 3 * 100, 75.0]

    def test_ranks_and_means_match_a_plain_count_over_each_year(self):
        rng = np.random.default_rng(7)
        for _ in range(8):
            count = int(rng.integers(1, 800))
            # Irregular starts across three years, rounded values so that ties occur
            starts = pd.DatetimeIndex(np.sort(
                pd.Timestamp('2016-01-01', tz='UTC')
                + pd.to_timedelta(rng.choice(3 * 365 * 24, count, replace=False), unit='h')))
            values = np.round(rng.lognormal(0, 1, count), 2)
            means, ranks = VolatilityProfileAnalyzer._trailing_year_reference(list(starts), values)

            assert np.array_equal(ranks, _ranked_one_by_one(starts, values))
            expected = [values[(starts > s - _YEAR) & (starts <= s)].mean() for s in starts]
            assert np.allclose(means, expected, rtol=1e-12)

    def test_a_year_forgets_what_lies_before_it(self):
        starts = pd.date_range('2016-01-01', periods=800, freq='D', tz='UTC')
        values = np.where(np.arange(800) < 400, 1.0, 2.0)
        means, _ = VolatilityProfileAnalyzer._trailing_year_reference(list(starts), values)
        assert means[-1] == 2.0 and means[399] == 1.0

    def test_no_periods_give_no_reference(self):
        means, ranks = VolatilityProfileAnalyzer._trailing_year_reference([], np.array([]))
        assert len(means) == 0 and len(ranks) == 0


class TestTheProfileItBuilds:

    def test_every_period_carries_the_ratio_and_rank_of_its_own_atr_over_close(self, analyzer):
        periods = analyzer.get_periods('kraken_spot', 'BTCUSD', 'M5')
        assert periods, 'the fixture must produce periods, or this proves nothing'
        # Counted again by hand: each hour's mean ATR over close, against the hours up to it
        # (a hundred hours lie well inside one year)
        bars = analyzer._load_and_prepare_bars('kraken_spot', 'BTCUSD', 'M5')
        natr = bars.groupby(bars['timestamp'].dt.floor('1h'))['natr'].mean().dropna()
        starts = pd.DatetimeIndex([period.start_time for period in periods])
        values = natr.loc[starts].to_numpy()

        assert np.array_equal([p.atr_percentile for p in periods],
                              _ranked_one_by_one(starts, values))
        expanding = np.cumsum(values) / np.arange(1, len(values) + 1)
        assert np.allclose([p.atr for p in periods], values / expanding, rtol=1e-12)

    def test_a_one_hour_period_takes_the_session_of_its_hour(self, analyzer):
        periods = analyzer.get_periods('kraken_spot', 'BTCUSD', 'M5')
        for period in periods:
            assert period.session.value == get_session_from_utc_hour(period.start_time.hour)

    def test_a_price_that_grows_a_hundredfold_at_steady_volatility_reads_as_ordinary(
            self, tmp_path, monkeypatch):
        """Against the whole history's absolute ATR its first year read very low, its last very high."""
        bars = 400
        closes = 10.0 * np.exp(np.linspace(0.0, np.log(100.0), bars))
        analyzer = _analyzer(tmp_path, monkeypatch, pd.DataFrame({
            'timestamp': pd.date_range('2016-01-01', periods=bars, freq='D', tz='UTC'),
            'open': closes, 'high': closes * 1.01, 'low': closes * 0.99, 'close': closes,
            'volume': np.ones(bars), 'tick_count': np.full(bars, 10),
        }))
        periods = analyzer.get_periods('kraken_spot', 'BTCUSD', 'D1')

        assert {period.regime for period in periods} == {VolatilityRegime.MEDIUM}
        assert periods[-1].atr_absolute > 50 * periods[20].atr_absolute
