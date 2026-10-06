"""
FiniexTestingIDE - Live Executor Test Fixtures
Fixtures for LiveRequestProcessor + LiveTradeExecutor + MockBrokerAdapter tests.

Unlike backtesting suites, these tests do NOT require scenario execution.
MockOrderExecution provides pre-configured LiveTradeExecutor instances
with MockBrokerAdapter — no network, no config files, no tick data required.

Each test creates its own executor via function-scoped fixtures
to ensure complete isolation between tests.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    TimeoutConfig,
)
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.trading_env_types.order_types import OrderType

# =============================================================================
# MOCK EXECUTION FIXTURES (Function Scope — fresh per test)
# =============================================================================

@pytest.fixture
def timeout_config() -> TimeoutConfig:
    """Standard timeout config for tests."""
    return TimeoutConfig(order_timeout_seconds=30.0)


@pytest.fixture
def logger() -> GlobalLogger:
    """Logger instance for isolated tracker tests."""
    return GlobalLogger(name='LiveExecutorTest')


@pytest.fixture
def request_processor(logger, timeout_config) -> LiveRequestProcessor:
    """Fresh LiveRequestProcessor for isolated unit tests."""
    return LiveRequestProcessor(logger=logger, timeout_config=timeout_config)


@pytest.fixture
def mock_instant() -> MockOrderExecution:
    """MockOrderExecution in INSTANT_FILL mode."""
    return MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)


@pytest.fixture
def mock_delayed() -> MockOrderExecution:
    """MockOrderExecution in DELAYED_FILL mode."""
    return MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)


@pytest.fixture
def mock_reject() -> MockOrderExecution:
    """MockOrderExecution in REJECT_ALL mode."""
    return MockOrderExecution(mode=MockExecutionMode.REJECT_ALL)


@pytest.fixture
def mock_timeout() -> MockOrderExecution:
    """MockOrderExecution in TIMEOUT mode (orders never fill)."""
    return MockOrderExecution(mode=MockExecutionMode.TIMEOUT)


@pytest.fixture
def executor_instant(mock_instant) -> LiveTradeExecutor:
    """LiveTradeExecutor with instant fill mock adapter."""
    return mock_instant.create_executor()


@pytest.fixture
def executor_delayed(mock_delayed) -> LiveTradeExecutor:
    """LiveTradeExecutor with delayed fill mock adapter."""
    return mock_delayed.create_executor()


@pytest.fixture
def executor_reject(mock_reject) -> LiveTradeExecutor:
    """LiveTradeExecutor with reject-all mock adapter."""
    return mock_reject.create_executor()


@pytest.fixture
def executor_timeout(mock_timeout) -> LiveTradeExecutor:
    """LiveTradeExecutor with TIMEOUT mock adapter (orders never fill)."""
    return mock_timeout.create_executor()


class LevelRecorder:
    """Logger stand-in that only remembers which level each line was written at."""

    def __init__(self):
        self.levels = []

    def verbose(self, message): self.levels.append('verbose')

    def debug(self, message): self.levels.append('debug')

    def info(self, message): self.levels.append('info')

    def warning(self, message): self.levels.append('warning')

    def error(self, message): self.levels.append('error')


# =============================================================================
# A VENUE THAT ACKNOWLEDGES FIRST AND FILLS ON ITS OWN
# =============================================================================

class AcknowledgingVenueMock(MockBrokerAdapter):
    """
    A venue shaped like Kraken: a market order is answered with a reference alone, and the
    venue fills it whatever happens to that answer. Only a later status read shows the fill.

    The stock mock answers a fill IN the submit answer (INSTANT_FILL), and its injected
    faults raise BEFORE anything changes at the venue — so no suite could reach the paths
    where the venue acted and we never heard. This one can: it executes first and loses the
    answer afterwards, on submit, on a status read, or both.
    """

    _STATUS_MAP = {**MockBrokerAdapter._STATUS_MAP, 'EXPIRED': BrokerOrderStatus.EXPIRED}

    def __init__(self):
        super().__init__(mode=MockExecutionMode.DELAYED_FILL)
        # What the venue holds, by reference: status plus what executed
        self._venue_orders: Dict[str, Dict[str, Any]] = {}
        self._closed: List[BrokerOrder] = []
        self.fill_market_orders: bool = True
        self.lose_next_submit_answer: bool = False
        self.query_fault: Optional[Exception] = None
        self.submits: List[str] = []
        self.queries: List[str] = []
        self.cancels: List[str] = []

    def do_request_submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Take the order and — with `fill_market_orders` — execute it at once.

        Args:
            payload: The mock submit payload

        Returns:
            An acknowledgement carrying only the reference
        """
        self._raise_injected_fault('submit')
        self._order_counter += 1
        ref = f'VENUE-{self._order_counter:04d}'
        price = self._resolve_market_fill_price(
            payload['symbol'], payload['direction'], payload['expected_price'])
        lots = payload['lots']
        self.submits.append(ref)
        if self.fill_market_orders and payload['order_type'] == OrderType.MARKET:
            self._venue_orders[ref] = {
                'status': 'FILLED', 'fill_price': price, 'filled_lots': lots}
            self._record_mock_trades(
                broker_ref=ref, symbol=payload['symbol'], direction=payload['direction'],
                total_lots=lots, fill_price=price, is_maker=False)
            self._closed.append(BrokerOrder(
                broker_ref=ref, symbol=payload['symbol'], direction=payload['direction'],
                order_type=OrderType.MARKET, lots=lots, status=BrokerOrderStatus.FILLED,
                price=price, filled_lots=lots,
                client_order_id=payload.get('client_order_id')))
        else:
            self._venue_orders[ref] = {'status': 'PENDING'}
        if self.lose_next_submit_answer:
            self.lose_next_submit_answer = False
            raise ConnectionAttemptFailedError('read timeout after AddOrder', terminal=False)
        return {'status': 'PENDING', 'broker_ref': ref}

    def do_request_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Answer from what the venue holds; `query_fault` makes every read fail instead.

        Args:
            payload: The mock query payload

        Returns:
            The venue's current answer about the reference
        """
        ref = payload['broker_ref']
        self.queries.append(ref)
        if self.query_fault is not None:
            raise self.query_fault
        self._raise_injected_fault('query')
        order = self._venue_orders.get(ref)
        if order is None:
            return {'status': 'REJECTED', 'broker_ref': ref,
                    'rejection_reason': 'Unknown broker_ref'}
        return {'broker_ref': ref, **order}

    def do_request_cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cancel a working order; refuse one that has already ended, as Kraken does.

        Args:
            payload: The mock cancel payload

        Returns:
            A cancelled answer for a working order
        """
        self._raise_injected_fault('cancel')
        ref = payload['broker_ref']
        self.cancels.append(ref)
        order = self._venue_orders.get(ref)
        if order is None or order['status'] != 'PENDING':
            # The adapter turns a venue-level error into a plain ConnectionError
            raise ConnectionError('Kraken API error: [EOrder:Unknown order]')
        order['status'] = 'CANCELLED'
        return {'status': 'CANCELLED', 'broker_ref': ref}

    def set_venue_status(
        self,
        ref: str,
        status: str,
        filled_lots: Optional[float] = None,
        fill_price: Optional[float] = None,
    ) -> None:
        """
        Make the venue hold an order in the given state — as if it moved without us.

        Args:
            ref: The order's reference
            status: 'PENDING', 'FILLED', 'CANCELLED' or 'EXPIRED'
            filled_lots: What it executed, if anything
            fill_price: At what price
        """
        self._venue_orders[ref] = {
            'status': status, 'filled_lots': filled_lots, 'fill_price': fill_price}

    def get_closed_broker_orders(
        self,
        start: datetime,
        end: datetime,
        client_order_id: Optional[str] = None,
    ) -> List[BrokerOrder]:
        """The orders this venue executed, narrowed to a key when one is given."""
        return [o for o in self._closed
                if client_order_id is None or o.client_order_id == client_order_id]

    def get_broker_orders(self) -> List[BrokerOrder]:
        """A filled market order never rests."""
        return []

