"""
FiniexTestingIDE - Shared Fixture Helpers
Plain functions for scenario execution and data extraction.

Used by suite-specific conftest.py files to avoid duplication.
Each suite's conftest.py creates pytest fixtures from these helpers
with its own config file path.

Convention:
- Functions here are NOT pytest fixtures (no decorators)
- Each returns a typed object for test assertions
- Suite conftest.py wraps these in @pytest.fixture(scope="session")
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.trading_env_types.pending_order_stats_types import PendingOrderStats
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats


# =============================================================================
# SCENARIO EXECUTION
# =============================================================================

def run_scenario(config_filename: str) -> BatchExecutionSummary:
    """
    Execute a backtesting scenario set and return full results.

    Args:
        config_filename: Config file relative to scenario_sets/ dir
                         e.g. "mvp_backtesting_validation_test.json"
                         or   "backtesting/multi_position_test.json"

    Returns:
        BatchExecutionSummary with all execution results
    """
    config_loader = ScenarioConfigLoader()
    scenario_config = config_loader.load_config(config_filename)

    app_config = AppConfigManager()
    scenario_set = ScenarioSet(scenario_config, app_config)

    orchestrator = BatchOrchestrator(scenario_set, app_config)
    summary = orchestrator.run()

    return summary


# =============================================================================
# DATA EXTRACTION (from BatchExecutionSummary → typed objects)
# =============================================================================

def extract_process_result(
    summary: BatchExecutionSummary,
    scenario_index: int = 0
) -> ProcessResult:
    """Extract ProcessResult for a specific scenario."""
    assert summary.process_result_list, "No process results in summary"
    assert scenario_index < len(summary.process_result_list), (
        f"Scenario index {scenario_index} out of range "
        f"(have {len(summary.process_result_list)} results)"
    )
    return summary.process_result_list[scenario_index]


def extract_tick_loop_results(process_result: ProcessResult) -> ProcessTickLoopResult:
    """Extract tick loop results from process result."""
    assert process_result.tick_loop_results, "No tick loop results"
    return process_result.tick_loop_results


def extract_backtesting_metadata(
    tick_loop_results: ProcessTickLoopResult
) -> BacktestingMetadata:
    """Extract BacktestingMetadata from decision statistics."""
    stats = tick_loop_results.decision_statistics
    assert stats.backtesting_metadata, "No backtesting metadata"
    return stats.backtesting_metadata


def extract_portfolio_stats(
    tick_loop_results: ProcessTickLoopResult
) -> PortfolioStats:
    """Extract portfolio statistics."""
    assert tick_loop_results.portfolio_stats, "No portfolio stats"
    return tick_loop_results.portfolio_stats


def extract_trade_history(
    tick_loop_results: ProcessTickLoopResult
) -> List[TradeRecord]:
    """Extract trade history for P&L verification."""
    assert tick_loop_results.trade_history is not None, "No trade history"
    return tick_loop_results.trade_history


def extract_pending_stats(
    tick_loop_results: ProcessTickLoopResult
) -> PendingOrderStats:
    """Extract pending order statistics."""
    assert tick_loop_results.pending_stats is not None, "No pending stats"
    return tick_loop_results.pending_stats


def extract_execution_stats(
    tick_loop_results: ProcessTickLoopResult
) -> ExecutionStats:
    """Extract execution statistics (order counts, SL/TP triggers)."""
    assert tick_loop_results.execution_stats is not None, "No execution stats"
    return tick_loop_results.execution_stats


def extract_order_history(
    tick_loop_results: ProcessTickLoopResult
) -> List[OrderResult]:
    """Extract order history (executed + rejected orders)."""
    assert tick_loop_results.order_history is not None, "No order history"
    return tick_loop_results.order_history


# =============================================================================
# CONFIG LOADING (raw JSON for test assertions)
# =============================================================================

def load_scenario_config(config_filename: str) -> Dict[str, Any]:
    """
    Load raw scenario config JSON for test assertions.

    Uses AppConfigManager to resolve the scenario_sets base path,
    same as ScenarioConfigLoader does internally.

    Args:
        config_filename: Config file relative to scenario_sets/ dir

    Returns:
        Parsed config dict
    """
    app_config = AppConfigManager()
    config_path = Path(app_config.get_scenario_sets_path()) / config_filename

    with open(config_path, 'r') as f:
        return json.load(f)


def extract_trade_sequence(scenario_config: Dict[str, Any]) -> list:
    """Extract trade sequence from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['trade_sequence']


def extract_seeds_config(scenario_config: Dict[str, Any]) -> Dict[str, int]:
    """Extract seeds from config."""
    return scenario_config['global']['trade_simulator_config']['seeds']


# =============================================================================
# EXPECTED VALUES (from config → test assertions)
# =============================================================================

@dataclass
class ScenarioExpectedValues:
    """Expected trade values extracted from scenario config.

    Args:
        stop_loss: Expected SL level (from trade_sequence or modify_sequence)
        take_profit: Expected TP level (from trade_sequence or modify_sequence)
        price: Expected limit/fill price (from trade_sequence or modify_limit_sequence)
        stop_price: Expected stop trigger price (from trade_sequence or modify_stop_sequence)

    Returns:
        Populated dataclass with effective values after modify overrides
    """
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    price: Optional[float] = None
    stop_price: Optional[float] = None


def extract_scenario_expected_values(
    config: Dict[str, Any],
    scenario_index: int
) -> ScenarioExpectedValues:
    """
    Extract effective expected values for a scenario.

    Reads trade_sequence for base values, then applies overrides
    from modify_sequence, modify_limit_sequence, or modify_stop_sequence.

    Args:
        config: Parsed scenario set config dict
        scenario_index: Index of scenario in scenarios list

    Returns:
        ScenarioExpectedValues with effective values after modifications
    """
    scenario = config['scenarios'][scenario_index]
    dlc = scenario.get('strategy_config', {}).get('decision_logic_config', {})
    trade = dlc['trade_sequence'][0]

    values = ScenarioExpectedValues(
        stop_loss=trade.get('stop_loss'),
        take_profit=trade.get('take_profit'),
        price=trade.get('price'),
        stop_price=trade.get('stop_price'),
    )

    # Modify sequences override the original values
    if 'modify_sequence' in dlc:
        mod = dlc['modify_sequence'][0]
        if 'take_profit' in mod:
            values.take_profit = mod['take_profit']
        if 'stop_loss' in mod:
            values.stop_loss = mod['stop_loss']

    if 'modify_limit_sequence' in dlc:
        mod = dlc['modify_limit_sequence'][0]
        if 'price' in mod:
            values.price = mod['price']

    if 'modify_stop_sequence' in dlc:
        mod = dlc['modify_stop_sequence'][0]
        if 'stop_price' in mod:
            values.stop_price = mod['stop_price']

    return values
