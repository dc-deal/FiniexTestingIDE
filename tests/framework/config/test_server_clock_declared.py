"""
Test that Every Broker Declares Its Server Clock.

The importer turns a venue's own timestamps into UTC through `server_clock`. It is a REQUIRED
field with no default, the same way `price_formation` is: a default is how the next broker's
ticks would be converted by another venue's clock, silently. MT5 stored every tick recorded in
US winter one hour early for months because its clock was written down as a fixed number.

So the model must refuse an entry without it, and the two shipped venues are pinned by name.
"""

import pytest
from pydantic import ValidationError

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.config_types.market_config_types import (
    BrokerEntryConfig,
    MarketType,
    PriceFormation,
    ServerClockConfig,
)


class TestTheFieldIsRequired:
    """A missing clock must fail at load, not resolve to UTC."""

    def test_an_entry_without_it_is_refused(self):
        """Price formation alone is not enough — the clock has to be stated too."""
        with pytest.raises(ValidationError):
            BrokerEntryConfig(
                broker_type='somevenue',
                market_type=MarketType.CRYPTO,
                price_formation=PriceFormation.ORDER_DRIVEN,
            )

    def test_a_clock_needs_both_halves(self):
        """A zone without its hours, or hours without a zone, is not a rule."""
        with pytest.raises(ValidationError):
            ServerClockConfig(timezone='America/New_York')
        with pytest.raises(ValidationError):
            ServerClockConfig(hours_ahead=7)


class TestEveryShippedBrokerDeclaresIt:
    """The configured brokers, read through the manager the way the importer reads them."""

    def test_every_broker_resolves_to_a_clock(self):
        """No broker may be missing it."""
        manager = MarketConfigManager()
        broker_types = manager.get_all_broker_types()

        assert broker_types, 'no brokers configured — the assertion below would be vacuous'
        for broker_type in broker_types:
            assert isinstance(manager.get_server_clock(broker_type), ServerClockConfig)

    @pytest.mark.parametrize('broker_type,timezone,hours_ahead', [
        # Kraken stamps its trades in UTC.
        ('kraken_spot', 'UTC', 0),
        # The MT5 server runs on New York close time: New York plus seven hours, so midnight
        # on the server is 17:00 in New York all year — UTC+2 in US winter, UTC+3 in summer.
        ('mt5', 'America/New_York', 7),
    ])
    def test_the_two_shipped_venues_are_declared_correctly(
            self, broker_type, timezone, hours_ahead):
        """Pinned by name: a wrong clock moves every tick of that venue by whole hours."""
        clock = MarketConfigManager().get_server_clock(broker_type)

        assert (clock.timezone, clock.hours_ahead) == (timezone, hours_ahead)
