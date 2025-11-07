from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_trading_api import DecisionTradingAPI
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.framework.types.process_data_types import ProcessScenarioConfig


def prepare_trade_simulator_for_scenario(logger: ScenarioLogger, config: ProcessScenarioConfig, decision_logic: AbstractDecisionLogic) -> TradeSimulator:
    """
    Create isolated TradeSimulator for a scenario.

    Each scenario gets its own TradeSimulator instance for:
    - Thread-safety in parallel execution
    - Independent balance/equity tracking
    - Clean statistics per scenario
    """

    # Extract configuration
    broker_config_path = config.broker_config_path
    if broker_config_path is None:
        raise ValueError(
            "No broker_config_path specified in strategy_config. "
            "Example: 'global.trade_simulator_config.broker_config_path': "
            "'./configs/brokers/mt5/ic_markets_demo.json'"
        )

    # Create broker config
    broker_config = BrokerConfig.from_json(broker_config_path)

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
        account_currency=config.account_currency,  # Changed from 'currency'
        symbol=config.symbol,  # NEW: Required for auto-detection
        logger=logger
    )

    # Create and validate DecisionTradingAPI
    # Interface for Decision Logic to interact with trading environment
    # why? Decision logic may not acess all of Trading Simulator, so
    # it will only exposed what's nessecary - and - aviable (order types).
    try:
        required_order_types = decision_logic.get_required_order_types()
        trading_api = DecisionTradingAPI(
            trade_simulator=trade_simulator,
            required_order_types=required_order_types
        )
        logger.debug(
            f"✅ DecisionTradingAPI validated for order types: "
            f"{[t.value for t in required_order_types]}"
        )
    except ValueError as e:
        logger.error(f"Order type validation failed: {e}")
        raise ValueError(
            f"Broker does not support required order types: {e}"
        )

    # 5. Inject DecisionTradingAPI into Decision Logic
    decision_logic.set_trading_api(trading_api)
    logger.debug(
        "✅ DecisionTradingAPI injected into Decision Logic")

    return trade_simulator
