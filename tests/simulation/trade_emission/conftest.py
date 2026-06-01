"""
Fixtures for sim trade emission tests (#326).

Directly instantiates a TradeSimulator with zero-latency MockBrokerAdapter
so MARKET orders fill on the first tick after open_order. Tests then inspect
pending.trades and cumulative_* fields on the filled order.
"""

from datetime import datetime, timezone

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType


@pytest.fixture
def sim_executor() -> TradeSimulator:
    """TradeSimulator with INSTANT_FILL Mock and zero latency."""
    adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
    broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
    logger = GlobalLogger('SimTradeEmissionTest')
    return TradeSimulator(
        broker_config=broker_config,
        initial_balance=10000.0,
        account_currency='USD',
        logger=logger,
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0,
        inbound_latency_max_ms=0,
    )


def feed_sim_tick(
    executor: TradeSimulator,
    msc: int,
    bid: float = 49999.0,
    ask: float = 50001.0,
    symbol: str = 'BTCUSD',
) -> TickData:
    """Feed a tick at the given msc to drive sim fill processing."""
    tick = TickData(
        timestamp=datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc),
        symbol=symbol,
        bid=bid,
        ask=ask,
        collected_msc=msc,
        time_msc=msc,
    )
    executor.on_tick(tick)
    return tick
