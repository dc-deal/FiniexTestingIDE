"""
FiniexTestingIDE - Reconciliation Test Fixtures (#151)

Fixtures + builders for Reconciler tests. The Reconciler only needs an object
exposing .broker.adapter, .portfolio.get_open_positions() and get_active_orders()
— a lightweight fake executor provides exactly that, so the diff logic is tested
in isolation. Broker truth comes from a MockBrokerAdapter (seeded + divergence).
No network, no config files.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List, Optional

import pytest

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.config_types.autotrader_defaults_config_types import (
    ReconciliationDefaults,
)
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import BrokerOrder, BrokerPosition
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOperation,
    PendingOrder,
    PendingOrderExecutionState,
    PendingOrderFills,
)
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.utils.connection_ladder import ConnectionLadder
from python.framework.utils.run_id_utils import build_client_order_id

# This session's discriminator in the reconciliation tests. Any four characters do —
# what the tests exercise is the difference between THIS one and another (#355).
_TEST_SESSION_KEY = '1641'

# =============================================================================
# Builders
# =============================================================================

def make_pending(
    order_id: str,
    broker_ref: Optional[str],
    symbol: str = 'ETHUSD',
    direction: OrderDirection = OrderDirection.LONG,
    lots: float = 0.01,
    limit_price: float = 2000.0,
    order_type: OrderType = OrderType.LIMIT,
    cumulative_filled_lots: float = 0.0,
    in_flight_operation: PendingOperation = PendingOperation.NONE,
) -> PendingOrder:
    """
    Build a local resting PendingOrder (what get_active_orders returns).

    in_flight_operation=PENDING_SUBMIT models the state #473 leaves behind when a submit
    answer is lost: the order is kept, its broker_ref is not.
    """
    return PendingOrder(
        pending_order_id=order_id,
        order_type=order_type,
        broker_ref=broker_ref,
        symbol=symbol,
        direction=direction,
        lots=lots,
        order_kwargs={'limit_price': limit_price},
        fills=PendingOrderFills(cumulative_filled_lots=cumulative_filled_lots),
        execution_state=PendingOrderExecutionState(in_flight_operation=in_flight_operation),
    )


def make_broker_order(
    broker_ref: str,
    symbol: str = 'ETHUSD',
    direction: OrderDirection = OrderDirection.LONG,
    lots: float = 0.01,
    price: float = 2000.0,
    order_type: OrderType = OrderType.LIMIT,
    status: BrokerOrderStatus = BrokerOrderStatus.PENDING,
    client_order_id: Optional[str] = None,
) -> BrokerOrder:
    """
    Build a broker-truth BrokerOrder.

    client_order_id is what the venue echoes back (#473). None models both a venue that
    reports no key and an order somebody else placed.
    """
    return BrokerOrder(
        broker_ref=broker_ref,
        symbol=symbol,
        direction=direction,
        order_type=order_type,
        lots=lots,
        status=status,
        price=price,
        client_order_id=client_order_id,
    )


def make_position(
    position_id: str,
    broker_ref: Optional[str],
    symbol: str = 'ETHUSD',
    direction: OrderDirection = OrderDirection.LONG,
    lots: float = 0.01,
    entry_price: float = 2000.0,
) -> Position:
    """Build a local shadow Position."""
    return Position(
        position_id=position_id,
        symbol=symbol,
        direction=direction,
        lots=lots,
        original_lots=lots,
        entry_price=entry_price,
        entry_time=datetime.now(timezone.utc),
        broker_ref=broker_ref,
    )


def make_broker_position(
    broker_ref: str,
    symbol: str = 'ETHUSD',
    direction: OrderDirection = OrderDirection.LONG,
    lots: float = 0.01,
    entry_price: float = 2000.0,
) -> BrokerPosition:
    """Build a broker-truth BrokerPosition."""
    return BrokerPosition(
        symbol=symbol,
        direction=direction,
        lots=lots,
        entry_price=entry_price,
        broker_ref=broker_ref,
    )


# =============================================================================
# Fake executor + fixtures
# =============================================================================

class FakeExecutor:
    """
    Minimal executor surface the Reconciler depends on.

    The client-order-id half (#355) uses the REAL key builder rather than a copy of the
    format — a fake that spelled the shape out a second time would keep passing after the
    real one changed.
    """

    def __init__(
        self,
        adapter: MockBrokerAdapter,
        active_orders: Optional[List[PendingOrder]] = None,
        positions: Optional[List[Position]] = None,
        rest_ladder: Optional[ConnectionLadder] = None,
        session_key: str = _TEST_SESSION_KEY,
    ):
        self.broker = SimpleNamespace(adapter=adapter)
        self._positions = list(positions or [])
        self.portfolio = SimpleNamespace(get_open_positions=lambda: list(self._positions))
        self._active_orders = list(active_orders or [])
        self._session_key = session_key
        self._rest_ladder = rest_ladder or ConnectionLadder(
            name='broker_rest',
            policy=ConnectionPolicy(),
            logger=GlobalLogger(name='ReconciliationTest'),
            transient=(ConnectionAttemptFailedError,),
        )

    def get_active_orders(self) -> List[PendingOrder]:
        return self._active_orders

    def get_rest_ladder(self) -> ConnectionLadder:
        return self._rest_ladder

    def get_session_key(self) -> str:
        return self._session_key

    def build_client_order_id(self, order_id: str) -> Optional[str]:
        return build_client_order_id(self._session_key, order_id)


@pytest.fixture
def logger() -> GlobalLogger:
    """Logger for isolated reconciliation tests."""
    return GlobalLogger(name='ReconciliationTest')


@pytest.fixture
def mock_adapter() -> MockBrokerAdapter:
    """Fresh MockBrokerAdapter (broker truth source)."""
    return MockBrokerAdapter()


@pytest.fixture
def make_reconciler(logger):
    """
    Factory: build a Reconciler over a FakeExecutor with seeded local state.

    Returns:
        Callable(adapter, active_orders, positions, trading_model, config, symbol,
        session_key)
    """
    def _make(
        adapter: MockBrokerAdapter,
        active_orders: Optional[List[PendingOrder]] = None,
        positions: Optional[List[Position]] = None,
        trading_model: TradingModel = TradingModel.SPOT,
        config: Optional[ReconciliationDefaults] = None,
        symbol: str = 'ETHUSD',
        session_key: str = _TEST_SESSION_KEY,
    ) -> Reconciler:
        executor = FakeExecutor(adapter, active_orders, positions, session_key=session_key)
        return Reconciler(
            executor=executor,
            config=config or ReconciliationDefaults(enabled=True),
            logger=logger,
            trading_model=trading_model,
            symbol=symbol,
        )
    return _make
