"""
MarketClock awareness tests (#365).

Rollover + weekend queries over a controllable clock and a real broker config —
forex (mt5) has swap + weekend closure; crypto spot (kraken) has neither.
"""

from datetime import datetime, timedelta, timezone

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.market_clock import MarketClock

_MT5 = 'configs/brokers/mt5/mt5_broker_config.json'
_KRAKEN = 'configs/brokers/kraken/kraken_spot_broker_config.json'


class _Clock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _mt5_clock(now: datetime) -> MarketClock:
    return MarketClock(_Clock(now).now, BrokerConfigFactory.build_broker_config(_MT5))


def test_forex_time_to_next_rollover():
    # Mon 12:00 UTC → next rollover Mon 22:00 UTC (winter) = 10h
    clock = _mt5_clock(_utc(2026, 1, 12, 12))
    assert clock.get_time_to_next_rollover() == timedelta(hours=10)


def test_forex_triple_on_wednesday():
    assert _mt5_clock(_utc(2026, 1, 14, 12)).is_next_rollover_triple('EURUSD') is True


def test_forex_not_triple_on_monday():
    assert _mt5_clock(_utc(2026, 1, 12, 12)).is_next_rollover_triple('EURUSD') is False


def test_forex_time_to_market_close_positive():
    ttc = _mt5_clock(_utc(2026, 1, 14, 12)).get_time_to_market_close()
    assert ttc is not None and ttc > timedelta(0)


def test_forex_is_weekend_ahead():
    # Fri 12:00 UTC, market closes Fri 20:00 UTC → 8h ahead
    clock = _mt5_clock(_utc(2026, 1, 16, 12))
    assert clock.is_weekend_ahead(timedelta(hours=12)) is True
    assert clock.is_weekend_ahead(timedelta(hours=2)) is False


def test_crypto_has_no_swap_or_weekend():
    clock = MarketClock(
        _Clock(_utc(2026, 1, 12, 12)).now,
        BrokerConfigFactory.build_broker_config(_KRAKEN))
    assert clock.get_time_to_next_rollover() is None
    assert clock.is_next_rollover_triple('BTCUSD') is False
    assert clock.get_time_to_market_close() is None
    assert clock.is_weekend_ahead(timedelta(days=7)) is False
