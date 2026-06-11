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

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.config_types.autotrader_defaults_config_types import ReconciliationDefaults
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import BrokerOrder, BrokerPosition
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder, PendingOrderFills
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType


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
) -> PendingOrder:
    """Build a local resting PendingOrder (what get_active_orders returns)."""
    return PendingOrder(
        pending_order_id=order_id,
        order_type=order_type,
        broker_ref=broker_ref,
        symbol=symbol,
        direction=direction,
        lots=lots,
        order_kwargs={'limit_price': limit_price},
        fills=PendingOrderFills(cumulative_filled_lots=cumulative_filled_lots),
    )


def make_broker_order(
    broker_ref: str,
    symbol: str = 'ETHUSD',
    direction: OrderDirection = OrderDirection.LONG,
    lots: float = 0.01,
    price: float = 2000.0,
    order_type: OrderType = OrderType.LIMIT,
    status: BrokerOrderStatus = BrokerOrderStatus.PENDING,
) -> BrokerOrder:
    """Build a broker-truth BrokerOrder."""
    return BrokerOrder(
        broker_ref=broker_ref,
        symbol=symbol,
        direction=direction,
        order_type=order_type,
        lots=lots,
        status=status,
        price=price,
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
    """Minimal executor surface the Reconciler depends on."""

    def __init__(
        self,
        adapter: MockBrokerAdapter,
        active_orders: Optional[List[PendingOrder]] = None,
        positions: Optional[List[Position]] = None,
    ):
        self.broker = SimpleNamespace(adapter=adapter)
        self._positions = list(positions or [])
        self.portfolio = SimpleNamespace(get_open_positions=lambda: list(self._positions))
        self._active_orders = list(active_orders or [])

    def get_active_orders(self) -> List[PendingOrder]:
        return self._active_orders


@pytest.fixture
def logger() -> GlobalLogger:
    """Logger for isolated reconciliation tests."""
    return GlobalLogger(name="ReconciliationTest")


@pytest.fixture
def mock_adapter() -> MockBrokerAdapter:
    """Fresh MockBrokerAdapter (broker truth source)."""
    return MockBrokerAdapter()


@pytest.fixture
def make_reconciler(logger):
    """
    Factory: build a Reconciler over a FakeExecutor with seeded local state.

    Returns:
        Callable(adapter, active_orders, positions, trading_model, config, symbol)
    """
    def _make(
        adapter: MockBrokerAdapter,
        active_orders: Optional[List[PendingOrder]] = None,
        positions: Optional[List[Position]] = None,
        trading_model: TradingModel = TradingModel.SPOT,
        config: Optional[ReconciliationDefaults] = None,
        symbol: str = 'ETHUSD',
    ) -> Reconciler:
        executor = FakeExecutor(adapter, active_orders, positions)
        return Reconciler(
            executor=executor,
            config=config or ReconciliationDefaults(enabled=True),
            logger=logger,
            trading_model=trading_model,
            symbol=symbol,
        )
    return _make
