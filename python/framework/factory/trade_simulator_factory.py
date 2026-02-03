from typing import List
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.decision_trading_api import DecisionTradingAPI
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.framework.types.order_types import OrderType
from python.framework.types.process_data_types import ProcessDataPackage, ProcessScenarioConfig


def prepare_trade_simulator_for_scenario(
    logger: ScenarioLogger,
    config: ProcessScenarioConfig,
    required_order_types: List[OrderType],
    shared_data: ProcessDataPackage
) -> TradeSimulator:
    """
    Create isolated TradeSimulator for a scenario.

    Args:
        logger: ScenarioLogger instance
        config: Scenario configuration
        required_order_types: Order types required by decision logic
        shared_data: Shared data package

    Returns:
        TradeSimulator instance ready for use
    """
    # Create broker config
    # Re-hydrate broker config from shared data (no file I/O!)
    # BrokerConfig was loaded once in main process and serialized
    broker_config = BrokerConfigFactory.from_serialized_dict(
        broker_type=config.broker_type,
        config_dict=shared_data.broker_configs.get(
            config.broker_type, None)
    )

    # Log currency configuration before TradeSimulator creation
    logger.info(
        f"💱 Trade Simulator Configuration:\n"
        f"   Symbol: {config.symbol}\n"
        f"   Account Currency: {config.account_currency}\n"
        f"   Initial Balance: {config.initial_balance}"
    )

    # Create NEW TradeSimulator for this scenario
    # Pass account_currency (supports "auto") and symbol for auto-detection
    trade_simulator = TradeSimulator(
        broker_config=broker_config,
        initial_balance=config.initial_balance,
        account_currency=config.account_currency,
        logger=logger,
        seeds=config.seeds
    )

    # Order type validation now happens in process_startup_preparation
    # via DecisionTradingAPI.__init__() with required_order_types

    return trade_simulator
