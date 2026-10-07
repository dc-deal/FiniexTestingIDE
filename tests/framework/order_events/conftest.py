"""
FiniexTestingIDE - Order-Event Test Helpers (#362)

Shared by the order-event suites: record what an executor writes, and read it back the way a
consumer does — each order's steps, grouped by the submission they belong to.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_event_types import OrderEvent
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)
from python.framework.types.trading_env_types.stress_test_types import StressTestConfig

BID = 49999.0
ASK = 50001.0


def record_events(executor: AbstractTradeExecutor) -> List[OrderEvent]:
    """
    Collect every event the executor records from now on.

    Args:
        executor: Either pipeline's executor

    Returns:
        The list the events are appended to
    """
    events: List[OrderEvent] = []
    executor.add_order_event_listener(events.append)
    return events


def order_lives(events: List[OrderEvent]) -> Dict[Optional[int], List[str]]:
    """
    Each order's steps in the order they happened, keyed by the submission they belong to.

    Args:
        events: Recorded events

    Returns:
        submitted_seq → event type values; a denial, never submitted, sits under None
    """
    lives: Dict[Optional[int], List[str]] = {}
    for event in events:
        lives.setdefault(event.submitted_seq, []).append(event.event_type.value)
    return lives


def order_steps(events: List[OrderEvent]) -> List[List[str]]:
    """
    Each order's steps, in the order the orders were first seen.

    Args:
        events: Recorded events

    Returns:
        One list of event type values per order
    """
    return list(order_lives(events).values())


def limit_order(price: float = 40000.0) -> OpenOrderRequest:
    """
    A resting BUY limit, far below the market by default.

    Args:
        price: The limit

    Returns:
        The request
    """
    return OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.LIMIT,
                            direction=OrderDirection.LONG, lots=0.01, price=price)


def market_order(lots: float = 0.01, stop_loss: Optional[float] = None) -> OpenOrderRequest:
    """
    A market BUY, with a stop-loss where one is given.

    Args:
        lots: Its size
        stop_loss: Its protective level, or None

    Returns:
        The request
    """
    return OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.MARKET,
                            direction=OrderDirection.LONG, lots=lots, stop_loss=stop_loss)


def live_session(mode: MockExecutionMode = MockExecutionMode.DELAYED_FILL,
                 spot_mode: bool = False):
    """
    A mock live session with one tick fed and its events recorded.

    Args:
        mode: How the mock venue answers
        spot_mode: The account model

    Returns:
        (mock, executor, events)
    """
    mock = MockOrderExecution(
        mode=mode, spot_mode=spot_mode,
        initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None,
        initial_balance=100000.0)
    executor = mock.create_executor()
    mock.feed_tick(executor, bid=BID, ask=ASK)
    return mock, executor, record_events(executor)


def make_simulator(spot_mode: bool, latency_ms: int = 0,
                   stress: Optional[StressTestConfig] = None,
                   initial_balance: float = 100000.0) -> TradeSimulator:
    """
    The simulation executor with one BTCUSD tick fed at 1000 ms.

    Args:
        spot_mode: The account model
        latency_ms: The inbound latency, fixed
        stress: A stress-test configuration, or None
        initial_balance: The account currency's balance

    Returns:
        The simulator, ready to take orders
    """
    sim = TradeSimulator(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT,
                                   MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=initial_balance,
        account_currency='USD',
        logger=GlobalLogger('OrderEventsSim'),
        seeds={'inbound_latency_seed': 42},
        stress_test_config=stress,
        inbound_latency_min_ms=latency_ms,
        inbound_latency_max_ms=latency_ms,
        spot_mode=spot_mode,
        initial_balances={'USD': initial_balance, 'BTC': 0.0} if spot_mode else None,
    )
    sim_tick(sim, msc=1000)
    return sim


def sim_tick(sim: TradeSimulator, msc: int, bid: float = BID, ask: float = ASK) -> None:
    """
    One BTCUSD tick at a millisecond stamp.

    Args:
        sim: The simulator
        msc: The tick's stamp
        bid: The bid
        ask: The ask
    """
    sim.on_tick(TickData(
        timestamp=datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc),
        symbol='BTCUSD', bid=bid, ask=ask, collected_msc=msc, time_msc=msc,
    ))
