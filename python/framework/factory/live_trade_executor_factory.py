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
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.live_types.live_execution_types import TimeoutConfig
from python.framework.utils.connection_ladder import ConnectionLadder


def build_live_executor(
    broker_config: BrokerConfig,
    balances: dict[str, float],
    account_currency: str,
    logger: AbstractLogger,
    timeout_config: Optional[TimeoutConfig] = None,
    spot_mode: bool = False,
    poll_interval_ms: int = 5000,
    connection_policy: Optional[ConnectionPolicy] = None,
    session_key: str = '',
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
        poll_interval_ms: Per-order async poll throttle in ms (#320, default 5000).
            Sourced from BrokerTransportConfig.poll_interval_ms when wired
            from autotrader_startup.
        connection_policy: Retry ladder and give-up rule for this broker's REST endpoint
            (#473). Sourced from BrokerTransportConfig.connection.
        session_key: Discriminator for the client order ids this session sends (#473).
            Empty on paths that never reach a venue.

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

    # One ladder for this broker's REST endpoint, handed to the executor so the order path
    # and the Reconciler classify the same 502 the same way (#473).
    rest_ladder = ConnectionLadder(
        name='broker_rest',
        policy=connection_policy or ConnectionPolicy(),
        logger=logger,
    )

    return LiveTradeExecutor(
        broker_config=broker_config,
        initial_balance=initial_balance,
        account_currency=account_currency,
        logger=logger,
        timeout_config=config,
        spot_mode=spot_mode,
        initial_balances=initial_balances,
        poll_interval_ms=poll_interval_ms,
        rest_ladder=rest_ladder,
        session_key=session_key,
    )
