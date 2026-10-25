"""
Test that Every Broker Declares Its Trading Model.

`trading_model` — spot or margin — decides the account model, and the account model is part of the
composite key's floor: no two accounts of one composite may differ in it (#615). It used to default
to margin, so a broker entry without it traded as margin unnoticed. It is now REQUIRED, like
`price_formation`, and asserted rather than trusted.
"""

import pytest
from pydantic import ValidationError

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.config_types.market_config_types import (
    BrokerEntryConfig,
    MarketType,
    PriceFormation,
    ServerClockConfig,
    TradingModel,
)


class TestTheFieldIsRequired:
    """A missing declaration fails at load instead of resolving to margin."""

    def test_an_entry_without_it_is_refused(self):
        with pytest.raises(ValidationError, match='trading_model'):
            BrokerEntryConfig(
                broker_type='somevenue',
                market_type=MarketType.CRYPTO,
                price_formation=PriceFormation.ORDER_DRIVEN,
                server_clock=ServerClockConfig(timezone='UTC', hours_ahead=0),
            )

    @pytest.mark.parametrize('trading_model', list(TradingModel))
    def test_an_entry_with_it_loads(self, trading_model):
        entry = BrokerEntryConfig(
            broker_type='somevenue',
            market_type=MarketType.CRYPTO,
            price_formation=PriceFormation.ORDER_DRIVEN,
            server_clock=ServerClockConfig(timezone='UTC', hours_ahead=0),
            trading_model=trading_model,
        )
        assert entry.trading_model is trading_model


class TestEveryShippedBrokerDeclaresIt:

    def test_every_broker_resolves_to_a_trading_model(self):
        manager = MarketConfigManager()
        broker_types = manager.get_all_broker_types()
        assert broker_types, 'no brokers configured — the assertion below would be vacuous'
        for broker_type in broker_types:
            assert isinstance(manager.get_trading_model(broker_type), TradingModel)

    @pytest.mark.parametrize('broker_type,expected', [
        ('kraken_spot', TradingModel.SPOT),
        ('mt5', TradingModel.MARGIN),
    ])
    def test_the_two_shipped_venues_are_declared_correctly(self, broker_type, expected):
        assert MarketConfigManager().get_trading_model(broker_type) is expected
