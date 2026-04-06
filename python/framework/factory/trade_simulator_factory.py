from typing import List
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_config_types import TradingModel
from python.framework.types.trading_env_types.order_types import OrderType
from python.framework.types.process_data_types import ProcessDataPackage, ProcessScenarioConfig


def prepare_trade_executor_for_scenario(
    logger: ScenarioLogger,
    config: ProcessScenarioConfig,
    required_order_types: List[OrderType],
    shared_data: ProcessDataPackage
) -> TradeSimulator:
    """
    Create isolated TradeSimulator for a backtesting scenario.

    This factory is for backtesting only (subprocess context).
    AutoTrader uses autotrader_startup.py → build_live_executor() directly.

    Args:
        logger: ScenarioLogger instance
        config: Scenario configuration
        required_order_types: Order types required by decision logic
        shared_data: Shared data package

    Returns:
        TradeSimulator instance
    """
    # Re-hydrate broker config from shared data (no file I/O!)
    broker_config = BrokerConfigFactory.from_serialized_dict(
        broker_type=config.broker_type,
        config_dict=shared_data.broker_configs.get(
            config.broker_type, None)
    )

    # Log configuration
    logger.info(
        f"💱 Trade Executor Configuration:\n"
        f"   Symbol: {config.symbol}\n"
        f"   Balances: {config.balances}"
    )

    # Log stress test config if any test is enabled
    if config.stress_test_config.has_any_enabled():
        logger.info(
            f"⚡ Stress Test Configuration: ACTIVE"
        )

    # Spot mode from trading_model
    spot_mode = config.trading_model == TradingModel.SPOT
    initial_balances = config.balances if spot_mode else None
    initial_balance = config.balances.get(config.account_currency, 0.0)

    return TradeSimulator(
        broker_config=broker_config,
        initial_balance=initial_balance,
        account_currency=config.account_currency,
        logger=logger,
        seeds=config.seeds,
        stress_test_config=config.stress_test_config,
        order_history_max=config.order_history_max,
        trade_history_max=config.trade_history_max,
        inbound_latency_min_ms=config.inbound_latency_min_ms,
        inbound_latency_max_ms=config.inbound_latency_max_ms,
        spot_mode=spot_mode,
        initial_balances=initial_balances,
    )
