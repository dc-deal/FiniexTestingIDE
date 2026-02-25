from typing import List
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor, ExecutorMode
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.framework.types.order_types import OrderType
from python.framework.types.process_data_types import ProcessDataPackage, ProcessScenarioConfig


def prepare_trade_executor_for_scenario(
    logger: ScenarioLogger,
    config: ProcessScenarioConfig,
    required_order_types: List[OrderType],
    shared_data: ProcessDataPackage
) -> AbstractTradeExecutor:
    """
    Create isolated trade executor for a scenario.

    Currently only supports "simulation" mode (TradeSimulator).
    Horizon 2 will add "live" mode (LiveTradeExecutor).

    Args:
        logger: ScenarioLogger instance
        config: Scenario configuration
        required_order_types: Order types required by decision logic
        shared_data: Shared data package

    Returns:
        AbstractTradeExecutor instance
    """
    # Create broker config
    # Re-hydrate broker config from shared data (no file I/O!)
    broker_config = BrokerConfigFactory.from_serialized_dict(
        broker_type=config.broker_type,
        config_dict=shared_data.broker_configs.get(
            config.broker_type, None)
    )

    # Determine executor mode from config
    executor_mode = ExecutorMode(config.executor_mode)

    # Log configuration
    logger.info(
        f"💱 Trade Executor Configuration:\n"
        f"   Mode: {executor_mode.value}\n"
        f"   Symbol: {config.symbol}\n"
        f"   Account Currency: {config.account_currency}\n"
        f"   Initial Balance: {config.initial_balance}"
    )

    # Log stress test config if any test is enabled
    if config.stress_test_config.has_any_enabled():
        logger.info(
            f"⚡ Stress Test Configuration: ACTIVE"
        )

    if executor_mode == ExecutorMode.SIMULATION:
        return TradeSimulator(
            broker_config=broker_config,
            initial_balance=config.initial_balance,
            account_currency=config.account_currency,
            logger=logger,
            seeds=config.seeds,
            stress_test_config=config.stress_test_config,
            order_history_max=config.order_history_max,
            trade_history_max=config.trade_history_max
        )

    raise ValueError(
        f"Unknown executor_mode: '{config.executor_mode}'. "
        f"Supported: 'simulation'. "
        f"Live trading will be available in Horizon 2."
    )


# Backwards compatibility alias
def prepare_trade_simulator_for_scenario(
    logger: ScenarioLogger,
    config: ProcessScenarioConfig,
    required_order_types: List[OrderType],
    shared_data: ProcessDataPackage
) -> AbstractTradeExecutor:
    """Backwards-compatible alias for prepare_trade_executor_for_scenario."""
    return prepare_trade_executor_for_scenario(
        logger=logger,
        config=config,
        required_order_types=required_order_types,
        shared_data=shared_data
    )
