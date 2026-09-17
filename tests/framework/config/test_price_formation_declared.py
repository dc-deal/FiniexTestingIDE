"""
Test that Every Broker Declares How Its Venue Forms Prices.

`price_formation` decides whether a traded price exists and therefore what a bar is rendered
from. It is deliberately a REQUIRED field with no default: a default is how the next broker
silently inherits the wrong basis, which is the defect the field exists to remove.

That only holds while nothing supplies one by accident, so it is asserted rather than trusted —
the model must refuse an entry without it, and every broker the project ships must carry it.
"""

import pytest
from pydantic import ValidationError

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.config_types.market_config_types import (
    BrokerEntryConfig,
    MarketType,
    PriceFormation,
)


class TestTheFieldIsRequired:
    """A missing declaration must fail at load, loudly, not resolve to a default."""

    def test_an_entry_without_it_is_refused(self):
        """The refusal is the whole mechanism — without it the field would be advisory."""
        with pytest.raises(ValidationError):
            BrokerEntryConfig(broker_type='somevenue', market_type=MarketType.CRYPTO)

    def test_an_entry_with_it_loads(self):
        """And a declared entry carries the value through untouched."""
        entry = BrokerEntryConfig(
            broker_type='somevenue',
            market_type=MarketType.CRYPTO,
            price_formation=PriceFormation.ORDER_DRIVEN,
        )

        assert entry.price_formation is PriceFormation.ORDER_DRIVEN

    def test_an_unknown_value_is_refused(self):
        """Only the two structures exist; a typo must not become a third."""
        with pytest.raises(ValidationError):
            BrokerEntryConfig(
                broker_type='somevenue',
                market_type=MarketType.CRYPTO,
                price_formation='order-driven',  # hyphen, not underscore
            )


class TestEveryShippedBrokerDeclaresIt:
    """
    The configured brokers, read through the manager the way production reads them.
    """

    def test_every_broker_resolves_to_a_known_structure(self):
        """No broker may be missing it, and none may resolve to something unexpected."""
        manager = MarketConfigManager()
        broker_types = manager.get_all_broker_types()

        assert broker_types, 'no brokers configured — the assertion below would be vacuous'
        for broker_type in broker_types:
            assert isinstance(manager.get_price_formation(broker_type), PriceFormation)

    @pytest.mark.parametrize('broker_type,expected', [
        # Kraken publishes a central order book and every trade prints at one price.
        ('kraken_spot', PriceFormation.ORDER_DRIVEN),
        # MT5 forex is dealer-quoted: no central venue, so no traded price exists at all
        # and the feed reports last = 0.0 on 100 % of ticks.
        ('mt5', PriceFormation.QUOTE_DRIVEN),
    ])
    def test_the_two_shipped_venues_are_declared_correctly(self, broker_type, expected):
        """
        Pinned by name, because getting one of these backwards is invisible today.

        On current data both resolve to the same bar prices — Kraken because bid == ask, MT5
        because the zero falls back to the midpoint — so a swapped declaration would change
        nothing until 1.6.0 data arrives and then change everything.
        """
        assert MarketConfigManager().get_price_formation(broker_type) is expected
