"""
FiniexTestingIDE - Cold-Start Test Fixtures (#355 Phase 2)

The executor here is the REAL LiveTradeExecutor over a MockBrokerAdapter, not a stand-in: the
adoption surface and the position counter are exactly what the boot step writes to, and a fake
would prove only that the fake works. No network, no config files.
"""

from pathlib import Path
from typing import List, Optional

import pytest

from python.framework.persistence.cold_start_state_store import ColdStartStateStore
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.config_types.autotrader_defaults_config_types import ColdStartDefaults
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType

# The discriminator an EARLIER session of this bot sent under — the carry-over is what makes it
# recognisable, and telling it from a stranger's key is the whole job.
PREVIOUS_SESSION_KEY = '8b3f'


class RecordingLogger:
    """
    Captures what reached the operator, so "was it said?" is directly assertable.

    Duck-typed rather than an AbstractLogger subclass — the base carries five abstract
    rendering hooks a test has no use for, and the same pattern is already in the #354 suite.
    """

    def __init__(self):
        self.infos: List[str] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def info(self, msg: str) -> None:
        self.infos.append(str(msg))

    def warning(self, msg: str) -> None:
        self.warnings.append(str(msg))

    def error(self, msg: str) -> None:
        self.errors.append(str(msg))

    def debug(self, msg: str) -> None:
        pass


def make_broker_order(
    broker_ref: str,
    client_order_id: Optional[str],
    symbol: str = 'BTCUSD',
    lots: float = 0.01,
    price: float = 40000.0,
    direction: OrderDirection = OrderDirection.LONG,
) -> BrokerOrder:
    """A resting order as the venue reports it."""
    return BrokerOrder(
        broker_ref=broker_ref,
        symbol=symbol,
        direction=direction,
        order_type=OrderType.LIMIT,
        lots=lots,
        status=BrokerOrderStatus.PENDING,
        price=price,
        client_order_id=client_order_id,
    )


@pytest.fixture
def logger() -> RecordingLogger:
    """Logger whose output the tests read back."""
    return RecordingLogger()


@pytest.fixture
def executor() -> LiveTradeExecutor:
    """A real live executor over a mock adapter that never fills."""
    return MockOrderExecution(mode=MockExecutionMode.TIMEOUT).create_executor()


@pytest.fixture
def spot_executor() -> LiveTradeExecutor:
    """
    A live executor whose portfolio runs in SPOT mode.

    The position book only exists for spot: a holding is a balance there, and a balance
    cannot describe itself as a position (#355). A margin executor would take the other
    branch and never touch the book at all.
    """
    return MockOrderExecution(
        mode=MockExecutionMode.TIMEOUT,
        spot_mode=True,
        initial_balances={'USD': 1000.0, 'BTC': 0.0},
    ).create_executor()


@pytest.fixture
def store(tmp_path: Path, logger: RecordingLogger) -> ColdStartStateStore:
    """A carry-over store in an isolated directory."""
    return ColdStartStateStore(
        root=tmp_path / 'cold_start_state',
        profile='btcusd_test',
        symbol='BTCUSD',
        logger=logger,
        run_id='20260901_120000_abcdef12',
    )


@pytest.fixture
def config() -> ColdStartDefaults:
    """Defaults as app_config declares them."""
    return ColdStartDefaults()
