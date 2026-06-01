"""
Fixtures for sim async modify/cancel lifecycle tests (#318).

Directly instantiates a TradeSimulator with zero-latency MockBrokerAdapter
so submit-to-active transitions happen on the first tick after open_order.
Tests can then schedule modify/cancel and drive resolution by feeding
subsequent ticks with controlled msc values.
"""

from datetime import datetime, timezone
from typing import Optional

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType


@pytest.fixture
def sim_executor() -> TradeSimulator:
    """
    TradeSimulator backed by an INSTANT_FILL Mock with zero latency.

    Zero latency keeps the submit→active transition deterministic: after
    a single feed_tick following open_order, the order is either filled
    (if price triggers) or in _active_limit_orders / _active_stop_orders.
    """
    adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
    broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
    logger = GlobalLogger('SimModifyLifecycleTest')
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
    """
    Feed a tick at the given msc to advance the sim clock.

    Used to drive _process_pending_orders (Phase 0 resolve + Phase 1 latency
    drain + Phase 2/3 active polling). The msc value is what the latency
    simulator and the modify/cancel apply_at_msc comparisons use.
    """
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
