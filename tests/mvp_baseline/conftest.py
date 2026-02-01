"""
FiniexTestingIDE - MVP Baseline Test Fixtures
Shared fixtures for backtesting validation tests

Provides:
- Scenario execution fixture (runs once per session)
- Data extraction helpers
- Bar loading utilities
- Tick data loading for P&L validation
"""

import json
import pytest
from pathlib import Path
from typing import Dict, Any, List, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.broker_types import SymbolSpecification
from python.framework.types.portfolio_trade_record_types import TradeRecord
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.framework.types.scenario_set_types import ScenarioSet
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.trading_env.order_latency_simulator import SeededDelayGenerator
from python.data_management.index.bars_index_manager import BarsIndexManager

import pandas as pd


# =============================================================================
# SCENARIO EXECUTION FIXTURE (Session Scope)
# =============================================================================

@pytest.fixture(scope="session")
def batch_execution_summary() -> BatchExecutionSummary:
    """
    Execute MVP backtesting validation scenario once per test session.

    Returns:
        BatchExecutionSummary with all execution results
    """
    config_loader = ScenarioConfigLoader()
    scenario_config = config_loader.load_config(
        "mvp_backtesting_validation_test.json")

    app_config = AppConfigManager()
    scenario_set = ScenarioSet(scenario_config, app_config)

    orchestrator = BatchOrchestrator(scenario_set, app_config)
    summary = orchestrator.run()

    return summary


@pytest.fixture(scope="session")
def process_result(batch_execution_summary: BatchExecutionSummary) -> ProcessResult:
    """
    Extract first scenario ProcessResult from batch summary.

    Args:
        batch_execution_summary: Executed batch summary

    Returns:
        ProcessResult for first scenario
    """
    assert batch_execution_summary.process_result_list, "No process results"
    return batch_execution_summary.process_result_list[0]


@pytest.fixture(scope="session")
def tick_loop_results(process_result: ProcessResult) -> ProcessTickLoopResult:
    """
    Extract tick loop results from process result.

    Args:
        process_result: Scenario process result

    Returns:
        ProcessTickLoopResult with all execution data
    """
    assert process_result.tick_loop_results, "No tick loop results"
    return process_result.tick_loop_results


@pytest.fixture(scope="session")
def backtesting_metadata(tick_loop_results: ProcessTickLoopResult) -> BacktestingMetadata:
    """
    Extract BacktestingMetadata from decision statistics.

    Args:
        tick_loop_results: Tick loop execution results

    Returns:
        BacktestingMetadata with validation data
    """
    stats = tick_loop_results.decision_statistics
    assert stats.backtesting_metadata, "No backtesting metadata"
    return stats.backtesting_metadata


@pytest.fixture(scope="session")
def portfolio_stats(tick_loop_results: ProcessTickLoopResult) -> PortfolioStats:
    """
    Extract portfolio statistics from tick loop results.

    Args:
        tick_loop_results: Tick loop execution results

    Returns:
        PortfolioStats with trading results
    """
    assert tick_loop_results.portfolio_stats, "No portfolio stats"
    return tick_loop_results.portfolio_stats


@pytest.fixture(scope="session")
def trade_history(tick_loop_results: ProcessTickLoopResult) -> List[TradeRecord]:
    """
    Extract trade history from tick loop results.

    Provides complete audit trail for P&L verification.

    Args:
        tick_loop_results: Tick loop execution results

    Returns:
        List of TradeRecord with full calculation details
    """
    assert tick_loop_results.trade_history is not None, "No trade history"
    return tick_loop_results.trade_history


# =============================================================================
# CONFIG FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def scenario_config() -> Dict[str, Any]:
    """
    Load raw scenario config for test assertions.

    Returns:
        Parsed config dict from JSON
    """
    config_path = Path(
        "./configs/scenario_sets/mvp_backtesting_validation_test.json")
    with open(config_path, 'r') as f:
        return json.load(f)


@pytest.fixture(scope="session")
def trade_sequence(scenario_config: Dict[str, Any]) -> list:
    """
    Extract trade sequence from config.

    Args:
        scenario_config: Raw scenario config

    Returns:
        List of trade specifications
    """
    return scenario_config['global']['strategy_config']['decision_logic_config']['trade_sequence']


@pytest.fixture(scope="session")
def seeds_config(scenario_config: Dict[str, Any]) -> Dict[str, int]:
    """
    Extract seeds from config.

    Args:
        scenario_config: Raw scenario config

    Returns:
        Dict with api_latency_seed and market_execution_seed
    """
    return scenario_config['global']['trade_simulator_config']['seeds']

# =============================================================================
# DELAY GENERATOR FIXTURES
# =============================================================================


@pytest.fixture(scope="function")
def api_delay_generator(seeds_config: Dict[str, int]) -> SeededDelayGenerator:
    """
    Create fresh API delay generator with config seed.

    Function scope - fresh generator per test.

    Args:
        seeds_config: Seeds from config

    Returns:
        SeededDelayGenerator for API latency
    """
    return SeededDelayGenerator(
        seed=seeds_config['api_latency_seed'],
        min_delay=1,
        max_delay=3
    )


@pytest.fixture(scope="function")
def exec_delay_generator(seeds_config: Dict[str, int]) -> SeededDelayGenerator:
    """
    Create fresh execution delay generator with config seed.

    Function scope - fresh generator per test.

    Args:
        seeds_config: Seeds from config

    Returns:
        SeededDelayGenerator for market execution
    """
    return SeededDelayGenerator(
        seed=seeds_config['market_execution_seed'],
        min_delay=2,
        max_delay=5
    )
