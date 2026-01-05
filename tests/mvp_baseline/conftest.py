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
from typing import Dict, Any, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.framework.types.scenario_set_types import ScenarioSet
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.performance_stats_types import DecisionLogicStats
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.trading_env.order_latency_simulator import SeededDelayGenerator
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.tick_index_manager import TickIndexManager

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
# BAR DATA FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def bars_index_manager() -> BarsIndexManager:
    """
    Initialize BarsIndexManager for bar file access.

    Returns:
        Configured BarsIndexManager
    """
    data_dir = Path("./data/parquet")
    manager = BarsIndexManager(data_dir)
    manager.build_index()
    return manager


@pytest.fixture(scope="session")
def prerendered_bars(
    bars_index_manager: BarsIndexManager,
    scenario_config: Dict[str, Any]
) -> Dict[str, pd.DataFrame]:
    """
    Load prerendered bars for snapshot comparison.

    Args:
        bars_index_manager: Bar index manager
        scenario_config: Scenario config for symbol extraction

    Returns:
        Dict mapping timeframe to DataFrame of bars
    """
    symbol = scenario_config['scenarios'][0]['symbol']

    bars = {}
    for timeframe in ['M5', 'M30']:
        bar_file = bars_index_manager.get_bar_file(symbol, timeframe)
        if bar_file:
            bars[timeframe] = pd.read_parquet(bar_file)

    return bars


# =============================================================================
# TICK DATA FIXTURES (For P&L Calculation)
# =============================================================================

@pytest.fixture(scope="session")
def tick_index_manager() -> TickIndexManager:
    """
    Initialize TickIndexManager for tick file access.

    Returns:
        Configured TickIndexManager
    """
    data_dir = Path("data/processed")
    manager = TickIndexManager(data_dir)
    manager.build_index()
    return manager


@pytest.fixture(scope="session")
def tick_dataframe(
    tick_index_manager: TickIndexManager,
    scenario_config: Dict[str, Any]
) -> pd.DataFrame:
    """
    Load tick data for P&L calculation validation.

    Args:
        tick_index_manager: Tick index manager
        scenario_config: Scenario config for symbol/date extraction

    Returns:
        DataFrame with tick data (bid, ask, timestamp)
    """
    scenario = scenario_config['scenarios'][0]
    symbol = scenario['symbol']

    start_date = scenario['start_date']
    end_date = scenario['end_date']
    max_ticks = scenario.get('max_ticks')

    # Normalize input dates to UTC timestamps
    start_dt = pd.to_datetime(start_date, utc=True)
    end_dt = pd.to_datetime(end_date, utc=True)
    tick_files = tick_index_manager.get_relevant_files(
        symbol=symbol,
        start_date=start_dt,
        end_date=end_dt
    )

    if not tick_files:
        pytest.skip(f"No tick files found for {symbol}")

    dfs = [pd.read_parquet(f) for f in tick_files]
    ticks_df = pd.concat(dfs, ignore_index=True)

    if max_ticks:
        ticks_df = ticks_df.head(max_ticks)

    return ticks_df


@pytest.fixture(scope="session")
def broker_symbol_spec(scenario_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Load broker symbol specification for P&L calculation.

    Args:
        scenario_config: Scenario config for broker path extraction

    Returns:
        Dict with digits, tick_size, tick_value, contract_size
    """
    broker_path = scenario_config['global']['trade_simulator_config']['broker_config_path']
    symbol = scenario_config['scenarios'][0]['symbol']

    with open(broker_path, 'r') as f:
        broker_data = json.load(f)

    symbols = broker_data.get('symbols', {})
    symbol_data = symbols.get(symbol, {})

    return {
        'symbol': symbol,
        'digits': symbol_data.get('digits', 5),
        'tick_size': symbol_data.get('tick_size', 0.00001),
        'tick_value': symbol_data.get('tick_value', 1.0),
        'contract_size': symbol_data.get('contract_size', 100000),
        'base_currency': symbol_data.get('base_currency', symbol[:3]),
        'quote_currency': symbol_data.get('quote_currency', symbol[3:])
    }


@pytest.fixture(scope="session")
def account_currency(scenario_config: Dict[str, Any]) -> str:
    """
    Extract account currency from config.

    Args:
        scenario_config: Raw scenario config

    Returns:
        Account currency string (e.g., 'USD', 'JPY')
    """
    currency = scenario_config['global']['trade_simulator_config'].get(
        'account_currency', 'auto')

    if currency == 'auto':
        symbol = scenario_config['scenarios'][0]['symbol']
        return symbol[3:6]

    return currency


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
