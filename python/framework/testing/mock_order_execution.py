# ============================================
# python/framework/testing/mock_order_execution.py
# ============================================
"""
FiniexTestingIDE - Mock Order Execution Test Utility
Higher-level utility for testing LiveTradeExecutor with MockBrokerAdapter.

Provides pre-configured LiveTradeExecutor instances and helper methods
for feeding ticks and asserting on order history.

Usage:
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
    executor = mock.create_executor()

    # Feed a tick (required before any order)
    mock.feed_tick(executor, symbol="BTCUSD", bid=49999.0, ask=50001.0)

    # Place an order
    result = executor.open_order("BTCUSD", OrderType.MARKET, OrderDirection.LONG, 0.001)

    # Feed another tick to trigger _process_pending_orders()
    mock.feed_tick(executor, symbol="BTCUSD", bid=50100.0, ask=50102.0)

    # Check results
    history = executor.get_order_history()
    stats = executor.get_execution_stats()
"""

from datetime import datetime, timezone
from typing import Optional

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live_trade_executor import LiveTradeExecutor
from python.framework.types.broker_types import BrokerType
from python.framework.types.live_execution_types import TimeoutConfig
from python.framework.types.market_data_types import TickData


class MockOrderExecution:
    """
    Test utility for LiveTradeExecutor with mock broker.

    Creates pre-configured executor instances and provides helpers
    for tick feeding and result inspection.
    """

    def __init__(
        self,
        mode: MockExecutionMode = MockExecutionMode.INSTANT_FILL,
        initial_balance: float = 10000.0,
        account_currency: str = "USD",
        timeout_seconds: float = 30.0,
    ):
        """
        Initialize mock execution environment.

        Args:
            mode: Mock broker execution behavior
            initial_balance: Starting account balance
            account_currency: Account currency
            timeout_seconds: Order timeout threshold
        """
        self._mode = mode
        self._initial_balance = initial_balance
        self._account_currency = account_currency
        self._timeout_config = TimeoutConfig(
            order_timeout_seconds=timeout_seconds,
        )
        self._tick_counter = 0

    def create_executor(self) -> LiveTradeExecutor:
        """
        Create a pre-configured LiveTradeExecutor with MockBrokerAdapter.

        Returns:
            LiveTradeExecutor ready for testing
        """
        adapter = MockBrokerAdapter(mode=self._mode)
        broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)

        logger = GlobalLogger(name="MockLiveTest")

        return LiveTradeExecutor(
            broker_config=broker_config,
            initial_balance=self._initial_balance,
            account_currency=self._account_currency,
            logger=logger,
            timeout_config=self._timeout_config,
        )

    def feed_tick(
        self,
        executor: LiveTradeExecutor,
        symbol: str = "BTCUSD",
        bid: float = 50000.0,
        ask: float = 50001.0,
        timestamp: Optional[datetime] = None,
    ) -> TickData:
        """
        Feed a tick to the executor (triggers on_tick → _process_pending_orders).

        Args:
            executor: LiveTradeExecutor instance
            symbol: Trading symbol
            bid: Bid price
            ask: Ask price
            timestamp: Tick timestamp (default: now UTC)

        Returns:
            The TickData that was fed
        """
        self._tick_counter += 1
        tick_time = timestamp or datetime.now(timezone.utc)

        tick = TickData(
            timestamp=tick_time,
            symbol=symbol,
            bid=bid,
            ask=ask,
        )

        executor.on_tick(tick)
        return tick
