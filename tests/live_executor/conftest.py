"""
FiniexTestingIDE - Live Executor Test Fixtures
Fixtures for LiveOrderTracker + LiveTradeExecutor + MockBrokerAdapter tests.

Unlike backtesting suites, these tests do NOT require scenario execution.
MockOrderExecution provides pre-configured LiveTradeExecutor instances
with MockBrokerAdapter — no network, no config files, no tick data required.

Each test creates its own executor via function-scoped fixtures
to ensure complete isolation between tests.
"""

import pytest

from python.framework.testing.mock_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.trading_env.live.live_order_tracker import LiveOrderTracker
from python.framework.logging.global_logger import GlobalLogger
from python.framework.types.live_execution_types import TimeoutConfig


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
    return GlobalLogger(name="LiveExecutorTest")


@pytest.fixture
def order_tracker(logger, timeout_config) -> LiveOrderTracker:
    """Fresh LiveOrderTracker for isolated unit tests."""
    return LiveOrderTracker(logger=logger, timeout_config=timeout_config)


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
