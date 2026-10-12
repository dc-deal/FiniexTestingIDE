"""
FiniexTestingIDE - Account Leverage (#586)

DecisionTradingApi.get_leverage called a method that exists nowhere as soon as an algo passed a
symbol. Leverage is account-wide at every venue this framework speaks to, so the answer is the
figure the margin calculation divides by — the broker configuration's own.
"""

import json

import pytest

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator


@pytest.mark.parametrize('broker_type', ['mt5', 'kraken_spot'])
def test_the_account_leverage_is_the_broker_configuration_s(broker_type):
    config_path = MarketConfigManager().get_broker_config_path(broker_type)
    with open(config_path, encoding='utf-8') as handle:
        configured = json.load(handle)['broker_info']['leverage']
    simulator = TradeSimulator(
        broker_config=BrokerConfigFactory.build_broker_config(config_path),
        initial_balance=10000.0,
        account_currency='USD',
        logger=GlobalLogger('AccountLeverage'),
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0,
        inbound_latency_max_ms=0,
    )

    api = DecisionTradingApi(simulator, required_order_types=[])

    assert api.get_leverage() == configured
