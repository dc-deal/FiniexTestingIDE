"""
Bar Period — the one answer to "is this bar finished at T?" (#588).

A bar is stamped with the instant its period OPENS and known only once the period has ENDED.
Every warmup path asks this module which bars exist at its start; these tests hold the answer
itself: the grid every timeframe shares, the boundary to the second, and the series form that
must agree with the scalar one.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from python.framework.types.market_types.market_data_types import Bar
from python.framework.utils.bar_period import (
    complete_at_mask,
    is_complete_at,
    last_complete_frame,
    open_time_of,
    period_end,
    period_start,
)
from python.framework.utils.timeframe_config_utils import TimeframeConfig

UTC = timezone.utc
REGISTRY = TimeframeConfig.sorted()
INSTANT = datetime(2026, 8, 5, 13, 47, 31, 250000, tzinfo=UTC)


class TestTheGrid:
    """Every timeframe shares one UTC grid anchored at midnight."""

    @pytest.mark.parametrize('timeframe', REGISTRY)
    def test_every_timeframe_divides_a_utc_day(self, timeframe):
        """A timeframe that did not divide a day would put its periods on a moving grid."""
        assert 1440 % TimeframeConfig.get_minutes(timeframe) == 0

    @pytest.mark.parametrize('timeframe', REGISTRY)
    def test_the_period_of_an_instant_contains_it(self, timeframe):
        start = period_start(INSTANT, timeframe)
        assert start <= INSTANT < period_end(start, timeframe)

    @pytest.mark.parametrize('timeframe', REGISTRY)
    def test_a_period_opens_on_the_grid(self, timeframe):
        midnight = INSTANT.replace(hour=0, minute=0, second=0, microsecond=0)
        offset = period_start(INSTANT, timeframe) - midnight
        assert offset.total_seconds() % (TimeframeConfig.get_minutes(timeframe) * 60) == 0

    def test_an_instant_on_a_boundary_opens_the_new_period(self):
        boundary = datetime(2026, 8, 5, 13, 0, tzinfo=UTC)
        assert period_start(boundary, 'H1') == boundary
        just_before = boundary - timedelta(microseconds=1)
        assert period_start(just_before, 'H1') == boundary - timedelta(hours=1)

    def test_a_day_opens_at_midnight_utc(self):
        assert period_start(INSTANT, 'D1') == datetime(2026, 8, 5, tzinfo=UTC)
        day = datetime(2026, 8, 5, tzinfo=UTC)
        assert period_end(day, 'D1') == datetime(2026, 8, 6, tzinfo=UTC)


class TestCompleteAt:
    """A bar is complete when its period has ended — never because its period has opened."""

    def test_a_bar_is_complete_exactly_when_its_period_ends(self):
        assert is_complete_at(datetime(2026, 8, 5, 12, tzinfo=UTC), 'H1',
                              datetime(2026, 8, 5, 13, tzinfo=UTC))

    def test_one_second_before_its_end_it_is_not(self):
        assert not is_complete_at(datetime(2026, 8, 5, 12, tzinfo=UTC), 'H1',
                                  datetime(2026, 8, 5, 12, 59, 59, tzinfo=UTC))

    @pytest.mark.parametrize('timeframe', REGISTRY)
    def test_the_bar_containing_an_instant_is_never_complete_at_it(self, timeframe):
        """The defect this module exists for: the bar holding the start read as finished."""
        assert not is_complete_at(period_start(INSTANT, timeframe), timeframe, INSTANT)

    def test_an_opening_before_the_start_does_not_make_a_bar_complete(self):
        """The rule the backtest used for 361 days, and the D1 bar it let through."""
        start = datetime(2026, 8, 5, 13, tzinfo=UTC)
        day = datetime(2026, 8, 5, tzinfo=UTC)
        assert day < start
        assert not is_complete_at(day, 'D1', start)

    @pytest.mark.parametrize('timeframe', ['M5', 'H4', 'D1'])
    def test_the_series_form_agrees_with_the_scalar_form(self, timeframe):
        minutes = TimeframeConfig.get_minutes(timeframe)
        opens = pd.Series(pd.date_range(
            INSTANT - timedelta(minutes=minutes * 5), periods=12,
            freq=f'{minutes}min', tz='UTC').floor(f'{minutes}min'))
        mask = complete_at_mask(opens, timeframe, INSTANT)
        assert list(mask) == [
            is_complete_at(opened.to_pydatetime(), timeframe, INSTANT) for opened in opens]
        assert mask.any() and not mask.all(), 'the sample must straddle the instant'


class TestSelection:
    """The selection a warmup makes: the newest N bars complete at an instant."""

    def test_the_newest_complete_bars_are_taken(self):
        opens = pd.date_range('2026-08-05 09:00', periods=6, freq='1h', tz='UTC')
        frame = pd.DataFrame({'timestamp': opens, 'close': range(6)})
        selected = last_complete_frame(frame, 'H1', datetime(2026, 8, 5, 13, 30, tzinfo=UTC), 2)
        assert list(selected['timestamp'].dt.hour) == [11, 12], (
            'the 13:00 bar holds the instant and must not be taken')

    def test_fewer_complete_bars_than_asked_returns_what_there_is(self):
        opens = pd.date_range('2026-08-05 09:00', periods=3, freq='1h', tz='UTC')
        frame = pd.DataFrame({'timestamp': opens, 'close': range(3)})
        selected = last_complete_frame(frame, 'H1', datetime(2026, 8, 5, 11, 30, tzinfo=UTC), 5)
        assert list(selected['timestamp'].dt.hour) == [9, 10]

    def test_a_bar_names_its_opening_instant(self):
        bar = Bar(symbol='ETHUSD', timeframe='M5', timestamp='2026-08-05T13:05:00+00:00',
                  open=1.0, high=1.0, low=1.0, close=1.0, volume=0.0)
        assert open_time_of(bar) == datetime(2026, 8, 5, 13, 5, tzinfo=UTC)
