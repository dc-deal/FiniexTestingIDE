"""
FiniexTestingIDE - Trade Simulator Creator
Creates isolated TradeSimulator instances for scenarios
"""

from python.framework.types.scenario_set_types import SingleScenario
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.trade_simulator import TradeSimulator


def create_trade_simulator_for_scenario(scenario: SingleScenario) -> TradeSimulator:
    """
    Create isolated TradeSimulator for a scenario.

    Each scenario gets its own TradeSimulator instance for:
    - Thread-safety in parallel execution
    - Independent balance/equity tracking
    - Clean statistics per scenario

    Reads configuration from scenario.trade_simulator_config:
    - broker_config_path: Path to broker configuration JSON
    - initial_balance: Starting balance (default: 10000.0)
    - currency: Account currency (default: "EUR")

    Args:
        scenario: SingleScenario with trade_simulator_config

    Returns:
        TradeSimulator instance configured for this scenario

    Raises:
        ValueError: If broker_config_path not specified

    Example:
        >>> scenario = SingleScenario(
        ...     trade_simulator_config={
        ...         "broker_config_path": "./configs/brokers/mt5/ic_markets.json",
        ...         "initial_balance": 5000.0,
        ...         "currency": "USD"
        ...     }
        ... )
        >>> simulator = create_trade_simulator_for_scenario(scenario)
        >>> simulator.portfolio.balance
        5000.0
    """
    # Get scenario-specific config (can override global)
    ts_config = scenario.trade_simulator_config or {}

    # Extract configuration
    broker_config_path = ts_config.get("broker_config_path")
    if broker_config_path is None:
        raise ValueError(
            "No broker_config_path specified in strategy_config. "
            "Example: 'global.trade_simulator_config.broker_config_path': "
            "'./configs/brokers/mt5/ic_markets_demo.json'"
        )

    initial_balance = ts_config.get("initial_balance", 10000.0)
    currency = ts_config.get("currency", "EUR")

    # Create broker config
    broker_config = BrokerConfig.from_json(broker_config_path)

    # Create NEW TradeSimulator for this scenario
    return TradeSimulator(
        broker_config=broker_config,
        initial_balance=initial_balance,
        currency=currency
    )
