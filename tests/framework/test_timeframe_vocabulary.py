"""
The timeframe vocabulary and what a venue will serve from it — one table, one venue fact.

Three different questions hide behind "which timeframes are there", and keeping them apart is
what this pins:

    what a name MEANS          TimeframeConfig._REGISTRY — a definition, not configurable
    what gets MATERIALIZED     import_config.json::processing.render_timeframes — a cost
    what a VENUE can SERVE     the adapter — a fact about the venue

Until 2026-09-14 the third was a second name->minutes table inside the Kraken OHLC fetcher, and
it fell out of step the first time the first one moved: M10 was added to the vocabulary, the
venue table knew nothing of it, and a live session would have discovered that on its first
fetch rather than at boot.

The venue set is now DERIVED — the venue declares which interval LENGTHS it publishes, and the
names come from the registry. So the two cannot disagree, and that is what these tests hold.
"""

import pytest

from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.exceptions.timeframe_errors import UnsupportedTimeframeError
from python.framework.utils.timeframe_config_utils import TimeframeConfig


class TestTheVenueSetIsDerivedNotListed:

    def test_every_served_timeframe_exists_in_the_vocabulary(self):
        """A venue cannot serve a name the project does not define."""
        for timeframe in KrakenOhlcBarFetcher.supported_warmup_timeframes():
            assert TimeframeConfig.exists(timeframe)

    def test_a_timeframe_is_served_exactly_when_its_duration_is_published(self):
        """
        The derivation itself, stated as a property. It is what replaces the second table:
        the venue declares interval LENGTHS, the names come from the registry.
        """
        published = KrakenOhlcBarFetcher.PUBLISHED_INTERVAL_MINUTES
        served = KrakenOhlcBarFetcher.supported_warmup_timeframes()

        for timeframe in TimeframeConfig.sorted():
            expected = TimeframeConfig.get_minutes(timeframe) in published
            assert (timeframe in served) is expected, timeframe

    def test_m10_is_an_archive_timeframe_and_not_a_live_one(self):
        """
        The concrete case that exposed the split. Kraken publishes no ten-minute OHLC
        interval, so M10 is renderable from ticks and servable over the API while remaining
        something no live session can warm up from. No configuration changes that.
        """
        assert TimeframeConfig.exists('M10')
        assert 'M10' not in KrakenOhlcBarFetcher.supported_warmup_timeframes()

    def test_asking_for_an_unservable_timeframe_names_the_reason(self):
        """
        It used to raise a bare ValueError listing a hand-kept set. A caller needs to know
        that the timeframe is fine and the VENUE is the limit — otherwise the obvious next
        move is to remove it from the vocabulary, which would be the wrong fix.
        """
        with pytest.raises(UnsupportedTimeframeError) as caught:
            KrakenOhlcBarFetcher().fetch_bars(symbol='BTCUSD', timeframe='M10', count=10)

        message = str(caught.value)
        assert '10-minute' in message
        assert 'renderable from' in message

    @pytest.mark.parametrize('timeframe', ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'])
    def test_the_conventional_timeframes_are_all_servable(self, timeframe):
        """A regression guard: the derivation must not quietly drop one that used to work."""
        assert timeframe in KrakenOhlcBarFetcher.supported_warmup_timeframes()
