# ============================================
# python/framework/factory/live_trade_executor_factory.py
# ============================================
"""
FiniexTestingIDE - Live Trade Executor Factory
Creates LiveTradeExecutor with proper dependency wiring.

Validates adapter is live-capable before constructing.
"""

from typing import Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import TimeoutConfig


def build_live_executor(
    broker_config: BrokerConfig,
    balances: dict[str, float],
    account_currency: str,
    logger: AbstractLogger,
    timeout_config: Optional[TimeoutConfig] = None,
    spot_mode: bool = False,
) -> LiveTradeExecutor:
    """
    Create a fully configured LiveTradeExecutor.

    Validates adapter capabilities and wires all dependencies.

    Args:
        broker_config: Broker configuration (adapter must be live-capable)
        balances: Asset balances (e.g., {'USD': 10000} or {'USD': 50.0, 'ETH': 0.0})
        account_currency: Account currency (e.g., 'USD')
        logger: Logger instance
        timeout_config: Order timeout thresholds (default: 30s timeout)
        spot_mode: Enable spot trading mode

    Returns:
        LiveTradeExecutor ready for live trading
    """
    # Validate adapter
    if not broker_config.adapter.is_live_capable():
        raise ValueError(
            f"Cannot create LiveTradeExecutor: adapter "
            f"'{broker_config.get_broker_name()}' is not live-capable. "
            f"Ensure adapter.is_live_capable() returns True."
        )

    config = timeout_config or TimeoutConfig()
    initial_balance = balances.get(account_currency, 0.0)
    initial_balances = balances if spot_mode else None

    return LiveTradeExecutor(
        broker_config=broker_config,
        initial_balance=initial_balance,
        account_currency=account_currency,
        logger=logger,
        timeout_config=config,
        spot_mode=spot_mode,
        initial_balances=initial_balances,
    )
