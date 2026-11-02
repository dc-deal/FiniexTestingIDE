"""
Rendered scenario set builder (#547) — what a backtest RUNS with, as one document.

A scenario set is the input to a cascade: the `app_config` layer under its global block, the
global block under each scenario, and a schema default for every parameter no file sets. The
run-config store freezes the file the run was given; this document is the result. Per scenario it
holds the strategy with every default filled in, each block the scenario's subprocess reads, and
what that subprocess reads outside the blocks — the broker's entry in `market_config.json` and the
market rules of its market type. At run level it holds the `backtesting` settings and the history
limits.

It is rendered where the run header is written, which names it, at the moment the source is
registered: a file edited while the run is in flight must not change what the run says it ran.
Nothing is constructed for it. The strategy goes through the renderer an AutoTrader session uses,
so the strategy of a session and of its backtest read alike; each block is laid over its model's
defaults, the values `ProcessScenarioConfig.from_scenario` falls back to.

A record, never a schema: nothing reads it back into a run. It names no run, time or path, so two
runs given the same configuration name the same document.
"""

from typing import Any, Dict, List

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.factory.strategy_config_renderer import render_strategy_config
from python.framework.types.config_types.autotrader_defaults_config_types import OrderGuardDefaults
from python.framework.types.config_types.backtesting_config_types import (
    DefaultScenarioExecutionConfig,
    TradeSimulatorDefaults,
)
from python.framework.types.config_types.robustness_config_types import RobustnessConfig
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.run_purpose_types import RunPurpose
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.order_types import OrderFeature
from python.framework.types.trading_env_types.stress_test_types import StressTestConfig
from python.framework.utils.config_fingerprint_utils import to_plain
from python.framework.utils.config_merge_utils import deep_merge


def render_scenario_set(
    scenarios: List[SingleScenario],
    run_purpose: RunPurpose,
    features_beyond_venue: List[OrderFeature],
    robustness: RobustnessConfig,
    app_config: AppConfigManager,
) -> Dict[str, Any]:
    """
    Render the configuration a backtest runs with.

    Args:
        scenarios: The run's scenarios, cascaded — a sweep combination's with its overrides applied
        run_purpose: What the run is for, as the set declares it
        features_beyond_venue: The order features the set declares its venue need not hold
        robustness: The set-wide robustness block
        app_config: Application configuration

    Returns:
        The rendered document, JSON-serialisable
    """
    market_config = MarketConfigManager()
    execution_defaults = app_config.get_scenario_execution_defaults()
    return {
        'kind': RunConfigKind.SCENARIO_SET_RENDERED.value,
        'run_purpose': run_purpose.value,
        'features_beyond_venue': [feature.value for feature in features_beyond_venue],
        'robustness': to_plain(robustness),
        'backtesting': {
            'execution': to_plain(app_config.get_execution_config()),
            'data_validation': to_plain(app_config.get_data_validation_config()),
            'history': {
                'bar_max_history': app_config.get_bar_max_history(),
                'order_history_max': app_config.get_order_history_max(),
                'trade_history_max': app_config.get_trade_history_max(),
            },
        },
        'scenarios': {
            scenario.name: render_scenario(scenario, execution_defaults, market_config)
            for scenario in scenarios
        },
    }


def render_scenario(
    scenario: SingleScenario,
    execution_defaults: Dict[str, Any],
    market_config: MarketConfigManager,
) -> Dict[str, Any]:
    """
    Render one scenario as its subprocess reads it.

    Args:
        scenario: The scenario, cascaded
        execution_defaults: The app's per-scenario execution defaults
        market_config: The market configuration, local override included

    Returns:
        The scenario's section of the document
    """
    return {
        'symbol': scenario.symbol,
        'data_broker_type': scenario.data_broker_type,
        'data_sentiment_type': scenario.data_sentiment_type,
        'start_date': scenario.start_date.isoformat(),
        'end_date': scenario.end_date.isoformat() if scenario.end_date else None,
        'max_ticks': scenario.max_ticks,
        'role': scenario.role.value,
        'regime': scenario.regime,
        'session': scenario.session,
        'strategy_config': render_strategy_config(scenario.strategy_config),
        'execution_config': _over_defaults(
            DefaultScenarioExecutionConfig(), scenario.resolve_execution_config(execution_defaults)),
        'trade_simulator_config': _over_defaults(
            TradeSimulatorDefaults(), scenario.trade_simulator_config or {}),
        'order_guard_config': to_plain(OrderGuardDefaults(**(scenario.order_guard_config or {}))),
        'stress_test_config': to_plain(StressTestConfig.from_dict(scenario.stress_test_config)),
        'market_entry': to_plain(market_config.get_broker_entry(scenario.data_broker_type)),
        'market_rules': to_plain(market_config.get_market_rules_for_broker(scenario.data_broker_type)),
    }


def _over_defaults(defaults: Any, block: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lay a block over its model's defaults, as the subprocess reads it: a value the block holds
    stands as given, a value it lacks — at any depth — is the model's.

    Args:
        defaults: The block's model, constructed with its defaults
        block: The block as the scenario holds it

    Returns:
        The complete block, JSON-serialisable
    """
    return to_plain(deep_merge(to_plain(defaults), block))
