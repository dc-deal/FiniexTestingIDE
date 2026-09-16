"""
Test the Bar Price Basis.

A bar is rendered from what its venue actually trades: the traded price where trades print
centrally, the bid/ask midpoint where they do not. Three properties are pinned here, and the
first one is the entire safety argument for re-rendering the archive.

**It is a no-op on every file currently on disk.** Kraken carries `last == bid == ask` below
collector format 1.6.0, and MT5 carries `last == 0.0` on 100 % of rows, so both resolve to
exactly what `mid` produced before. The change only starts to matter when a tick arrives whose
bid and ask differ — which is what 1.6.0 brings.

**A quote-driven venue must never render at zero.** MT5 reports `last = 0.0` because it has no
central place where trades happen. Nothing in this suite asserted that a bar price is positive
before, and `0 >= 0` satisfies the existing `high >= low` — so a misfiring gate would have
produced an archive of zeros in silence.
"""

import pandas as pd
import pytest

from python.data_management.importers.vectorized_bar_renderer import VectorizedBarRenderer
from python.framework.data_preparation.tick_parquet_reader import read_tick_parquet
from tests.framework.bar_rendering.conftest import (
    generate_ticks,
    ticks_to_dataframe,
)


def _with_last(df: pd.DataFrame, last_values) -> pd.DataFrame:
    """Re-derive `price` for a frame whose `last` column has been replaced.

    Mirrors read_tick_parquet's rule rather than restating it, so a fixture cannot drift
    away from the production resolution.

    Args:
        df: Tick frame carrying bid/ask
        last_values: Value or series to write into `last`

    Returns:
        The frame with `last` and a re-derived `price`
    """
    out = df.copy()
    out['last'] = last_values
    mid = (out['bid'] + out['ask']) / 2.0
    out['price'] = out['last'].where(out['last'] > 0.0, mid)
    return out


class TestTheChangeIsANoOpOnTodaysData:
    """
    The safety argument for re-rendering: both regimes on disk resolve to the old value.
    """

    def test_order_driven_without_a_spread_renders_what_mid_rendered(self):
        """
        Kraken below format 1.6.0: `bid == ask == last`, so `price` IS the midpoint.

        This is why the switch can land before the first 1.6.0 import and change nothing.
        """
        ticks = generate_ticks(count=300, bid_start=42000.0, spread=0.0)
        df = ticks_to_dataframe(ticks)
        # A pre-1.6.0 Kraken file: the trade price sits on both sides of the book.
        df = _with_last(df, df['bid'])

        assert (df['bid'] == df['ask']).all(), 'fixture must model a spread-less venue'
        assert (df['price'] == (df['bid'] + df['ask']) / 2.0).all()

    def test_quote_driven_falls_back_to_the_midpoint(self):
        """MT5 writes `last = 0.0` on every row; a zero is an absence, not a price."""
        ticks = generate_ticks(count=300, bid_start=1.1000, spread=0.0002)
        df = _with_last(ticks_to_dataframe(ticks), 0.0)

        assert (df['last'] == 0.0).all()
        assert (df['price'] == (df['bid'] + df['ask']) / 2.0).all()

    def test_a_real_spread_moves_the_bar_to_the_traded_price(self):
        """With 1.6.0 data the two bases finally differ — the case the switch exists for."""
        ticks = generate_ticks(count=120, bid_start=88000.0, spread=0.20)
        df = ticks_to_dataframe(ticks)
        traded = df['ask']  # a BUY lifts the ask, verified against real 1.6.0 files
        df = _with_last(df, traded)

        bars = VectorizedBarRenderer('BTCUSD').render_all_timeframes(df)['M1']
        mid_bars = VectorizedBarRenderer('BTCUSD').render_all_timeframes(
            _with_last(ticks_to_dataframe(ticks), 0.0))['M1']

        assert len(bars) == len(mid_bars)
        # Half a spread apart, on every bar, in the direction the taker traded.
        deltas = (bars['close'].to_numpy() - mid_bars['close'].to_numpy())
        assert (deltas > 0).all()
        assert deltas.max() == pytest.approx(0.10, abs=1e-6)


class TestBarPricesAreAlwaysPositive:
    """
    The assertion that catches a misfiring gate before it reaches an archive.

    `0 >= 0` satisfies `high >= low`, so a bar of zeros passes every structural check that
    existed before this one.
    """

    def test_quote_driven_bars_are_never_zero(self):
        """A venue with no traded price must still render real prices."""
        ticks = generate_ticks(count=300, bid_start=1.1000, spread=0.0002)
        df = _with_last(ticks_to_dataframe(ticks), 0.0)

        for timeframe, bars in VectorizedBarRenderer('EURUSD').render_all_timeframes(df).items():
            if bars.empty:
                continue
            for column in ('open', 'high', 'low', 'close'):
                assert (bars[column] > 0).all(), f'{timeframe}: {column} contains a zero'

    def test_order_driven_bars_are_never_zero(self):
        """The same guard on the side that does have a traded price."""
        ticks = generate_ticks(count=300, bid_start=42000.0, spread=0.0)
        df = _with_last(ticks_to_dataframe(ticks), ticks_to_dataframe(ticks)['bid'])

        for timeframe, bars in VectorizedBarRenderer('BTCUSD').render_all_timeframes(df).items():
            if bars.empty:
                continue
            for column in ('open', 'high', 'low', 'close'):
                assert (bars[column] > 0).all(), f'{timeframe}: {column} contains a zero'


class TestTheRendererRefusesAnUnnormalizedFrame:
    """
    The renderer is a pure transformation and does not resolve the basis itself.

    It runs in a worker pool, so resolving there would mean one config read per process —
    and two copies of one rule. It therefore requires what `read_tick_parquet` produces.
    """

    def test_a_frame_without_price_is_refused_by_name(self):
        """A caller that bypassed the reader gets told which entry point to use."""
        df = ticks_to_dataframe(generate_ticks(count=10)).drop(columns=['price'])

        with pytest.raises(ValueError, match='read_tick_parquet'):
            VectorizedBarRenderer('BTCUSD').render_all_timeframes(df)


class TestTheReaderIsTheSingleResolution:
    """`read_tick_parquet` derives `price`, so every consumer inherits one rule."""

    def test_reader_derives_price_for_a_file_without_last(self, tmp_path):
        """A pre-`last` archive file still gets a usable price column."""
        path = tmp_path / 'ticks.parquet'
        pd.DataFrame({
            'timestamp': pd.date_range('2026-01-15', periods=5, freq='1s', tz='UTC'),
            'bid': [1.1000] * 5,
            'ask': [1.1002] * 5,
        }).to_parquet(path)

        df = read_tick_parquet(path)

        assert 'price' in df.columns
        assert (df['price'] == 1.1001).all()

    def test_reader_prefers_the_traded_price_where_there_is_one(self, tmp_path):
        """And a zero in the same column still falls back, row by row."""
        path = tmp_path / 'ticks.parquet'
        pd.DataFrame({
            'timestamp': pd.date_range('2026-01-15', periods=3, freq='1s', tz='UTC'),
            'bid': [100.0, 100.0, 100.0],
            'ask': [100.2, 100.2, 100.2],
            'last': [100.2, 0.0, 100.0],
        }).to_parquet(path)

        df = read_tick_parquet(path)

        assert list(df['price']) == [100.2, 100.1, 100.0]
