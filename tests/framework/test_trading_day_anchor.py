"""
Trading Day Anchor — where a market's day flips, and which day an instant belongs to (#476)

Three places used to answer this from the tick stamp at midnight UTC: the session-log
rotation, the daily-loss baseline (#314) and — once it exists — the record seal. That is
right for crypto by coincidence and wrong for forex, whose day flips at the swap rollover.

What these tests pin is the pair that makes the answer trustworthy: the DST-aware boundary
(17:00 New York is 21:00 UTC in summer and 22:00 in winter, so a fixed offset would put
every winter fragment an hour out) and the off-by-one either side of it — an instant at
16:59 local still belongs to the day before, and mislabelling it is invisible in every
artifact it touches.
"""

from datetime import date, datetime, timezone

import pytest

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.exceptions.market_config_errors import TradingDayAnchorMissingError
from python.framework.types.config_types.market_config_types import (
    DayAnchorConfig,
    MarketRulesConfig,
    MarketType,
    PipMode,
    SwapRolloverConfig,
)
from python.framework.utils.trading_day_anchor import boundary_opening, trading_day_of

CRYPTO = DayAnchorConfig(local_time='00:00', timezone='UTC')
FOREX = DayAnchorConfig(local_time='17:00', timezone='America/New_York')


def _utc(text: str) -> datetime:
    """An ISO instant read as UTC."""
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class TestCryptoAnchor:
    """00:00 UTC — the whole calendar day carries one label."""

    @pytest.mark.parametrize('instant', [
        '2026-09-21T00:00:00',
        '2026-09-21T15:00:00',
        '2026-09-21T23:59:59',
    ])
    def test_the_calendar_day_is_the_trading_day(self, instant):
        assert trading_day_of(_utc(instant), CRYPTO) == date(2026, 9, 21)

    def test_one_second_before_midnight_is_the_day_before(self):
        assert trading_day_of(_utc('2026-09-20T23:59:59'), CRYPTO) == date(2026, 9, 20)

    def test_the_boundary_is_midnight(self):
        assert boundary_opening(date(2026, 9, 21), CRYPTO) == _utc('2026-09-21T00:00:00')


class TestForexAnchorAcrossDst:
    """17:00 America/New_York — the instant moves with daylight saving, the label does not."""

    def test_summer_boundary_is_21_utc(self):
        assert boundary_opening(date(2026, 9, 21), FOREX) == _utc('2026-09-21T21:00:00')

    def test_winter_boundary_is_22_utc(self):
        assert boundary_opening(date(2026, 12, 21), FOREX) == _utc('2026-12-21T22:00:00')

    @pytest.mark.parametrize('instant,expected', [
        # One minute before the summer rollover — still the previous trading day
        ('2026-09-21T20:59:00', date(2026, 9, 20)),
        # The rollover itself opens the new one
        ('2026-09-21T21:00:00', date(2026, 9, 21)),
        # The same wall-clock pair in winter, an hour later in UTC
        ('2026-12-21T21:59:00', date(2026, 12, 20)),
        ('2026-12-21T22:00:00', date(2026, 12, 21)),
    ])
    def test_the_day_flips_at_the_rollover_not_at_midnight(self, instant, expected):
        assert trading_day_of(_utc(instant), FOREX) == expected

    def test_midnight_utc_belongs_to_the_day_that_opened_the_evening_before(self):
        """The case that separates this module from a UTC date: 00:30 UTC on the 22nd is
        20:30 New York on the 21st, i.e. inside the session that opened at 17:00."""
        assert trading_day_of(_utc('2026-09-22T00:30:00'), FOREX) == date(2026, 9, 21)


def _rules(**overrides) -> MarketRulesConfig:
    """Market rules with the required fields filled and the anchors left to the caller."""
    return MarketRulesConfig(
        weekend_closure=True,
        session_bucketing=True,
        primary_activity_metric='tick_count',
        pip_mode=PipMode.FRACTIONAL_PIP,
        **overrides,
    )


class TestResolution:
    """
    Which anchor a broker gets, and what happens when nobody declared one.

    These build the rules rather than reading the merged config on purpose. An operator
    override in `user_configs/market_config.json` is legitimate — shifting the crypto anchor
    a few minutes ahead is how the live boundary is rehearsed — and a test that asserts the
    operator's current VALUES would go red for that, which is the wrong reason. What is
    pinned here is the resolution RULE; the smoke test below is all that touches real config.
    """

    def test_a_declared_anchor_wins(self, monkeypatch):
        manager = MarketConfigManager()
        monkeypatch.setattr(manager, 'get_market_type', lambda broker_type: MarketType.CRYPTO)
        monkeypatch.setattr(manager, 'get_market_rules', lambda market_type: _rules(
            trading_day_anchor=DayAnchorConfig(local_time='00:00', timezone='UTC'),
        ))

        anchor = manager.get_trading_day_anchor('some_broker')
        assert (anchor.local_time, anchor.timezone) == ('00:00', 'UTC')

    def test_without_one_the_swap_rollover_answers(self, monkeypatch):
        """So the forex instant exists exactly once in the config — as swap_rollover."""
        manager = MarketConfigManager()
        monkeypatch.setattr(manager, 'get_market_type', lambda broker_type: MarketType.FOREX)
        monkeypatch.setattr(manager, 'get_market_rules', lambda market_type: _rules(
            swap_rollover=SwapRolloverConfig(),
        ))

        anchor = manager.get_trading_day_anchor('some_broker')
        assert (anchor.local_time, anchor.timezone) == ('17:00', 'America/New_York')

    def test_a_declared_anchor_beats_a_swap_rollover_that_disagrees(self, monkeypatch):
        """A market that charges swap AND flips its day elsewhere is not a contradiction."""
        manager = MarketConfigManager()
        monkeypatch.setattr(manager, 'get_market_type', lambda broker_type: MarketType.FOREX)
        monkeypatch.setattr(manager, 'get_market_rules', lambda market_type: _rules(
            swap_rollover=SwapRolloverConfig(),
            trading_day_anchor=DayAnchorConfig(local_time='08:00', timezone='Europe/Berlin'),
        ))

        anchor = manager.get_trading_day_anchor('some_broker')
        assert (anchor.local_time, anchor.timezone) == ('08:00', 'Europe/Berlin')

    @pytest.mark.parametrize('broker', ['kraken_spot', 'mt5'])
    def test_both_shipped_brokers_resolve_to_something(self, broker):
        """The smoke half: whatever the operator configured, neither broker may be anchorless."""
        anchor = MarketConfigManager().get_trading_day_anchor(broker)
        assert anchor.local_time and anchor.timezone

    def test_a_market_with_neither_is_refused_rather_than_defaulted(self, monkeypatch):
        """A midnight-UTC default would be right for crypto and silently wrong for forex."""
        manager = MarketConfigManager()
        monkeypatch.setattr(manager, 'get_market_type', lambda broker_type: MarketType.FOREX)
        monkeypatch.setattr(manager, 'get_market_rules', lambda market_type: _rules())

        with pytest.raises(TradingDayAnchorMissingError) as excinfo:
            manager.get_trading_day_anchor('some_broker')

        assert 'trading_day_anchor' in str(excinfo.value)
        assert 'swap_rollover' in str(excinfo.value)
