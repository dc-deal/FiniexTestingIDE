"""
Fixtures for order-precision normalization tests (#332).

Instantiates a TradeSimulator backed by a zero-latency INSTANT_FILL
MockBrokerAdapter on Kraken Spot, so the open/modify/close paths exercise the
shared executor-layer precision normalization against real symbol specs
(BTCUSD: digits=1, volume_min=5e-05, volume_step=1e-08).
"""

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.trading_env_types.broker_types import BrokerType


@pytest.fixture
def sim_executor() -> TradeSimulator:
    """TradeSimulator on Kraken Spot with a zero-latency INSTANT_FILL mock."""
    adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
    broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
    logger = GlobalLogger('OrderPrecisionTest')
    return TradeSimulator(
        broker_config=broker_config,
        initial_balance=10000.0,
        account_currency='USD',
        logger=logger,
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0,
        inbound_latency_max_ms=0,
    )
