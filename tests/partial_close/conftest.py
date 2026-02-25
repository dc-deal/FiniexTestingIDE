"""
FiniexTestingIDE - Partial Close Test Fixtures (#119)
Suite-specific fixtures for backtesting/partial_close_test.json

All extraction logic lives in tests/shared/fixture_helpers.py.
This conftest only wires the config path and creates pytest fixtures.
"""

import pytest
from typing import Dict, Any, List

from python.framework.types.portfolio_trade_record_types import TradeRecord
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.utils.seeded_generators.seeded_delay_generator import SeededDelayGenerator

from tests.shared.fixture_helpers import (
    run_scenario,
    extract_process_result,
    extract_tick_loop_results,
    extract_backtesting_metadata,
    extract_portfolio_stats,
    extract_trade_history,
    load_scenario_config,
    extract_trade_sequence,
    extract_seeds_config,
)

# =============================================================================
# CONFIG: Which scenario set does this suite run?
# =============================================================================
PARTIAL_CLOSE_CONFIG = "backtesting/partial_close_test.json"


# =============================================================================
# SCENARIO EXECUTION (Session Scope)
# =============================================================================

@pytest.fixture(scope="session")
def batch_execution_summary() -> BatchExecutionSummary:
    """Execute partial close backtesting scenario once per session."""
    return run_scenario(PARTIAL_CLOSE_CONFIG)


@pytest.fixture(scope="session")
def process_result(batch_execution_summary: BatchExecutionSummary) -> ProcessResult:
    """Extract first scenario ProcessResult."""
    return extract_process_result(batch_execution_summary)


@pytest.fixture(scope="session")
def tick_loop_results(process_result: ProcessResult) -> ProcessTickLoopResult:
    """Extract tick loop results."""
    return extract_tick_loop_results(process_result)


@pytest.fixture(scope="session")
def backtesting_metadata(tick_loop_results: ProcessTickLoopResult) -> BacktestingMetadata:
    """Extract BacktestingMetadata from decision statistics."""
    return extract_backtesting_metadata(tick_loop_results)


@pytest.fixture(scope="session")
def portfolio_stats(tick_loop_results: ProcessTickLoopResult) -> PortfolioStats:
    """Extract portfolio statistics."""
    return extract_portfolio_stats(tick_loop_results)


@pytest.fixture(scope="session")
def trade_history(tick_loop_results: ProcessTickLoopResult) -> List[TradeRecord]:
    """Extract trade history for P&L verification."""
    return extract_trade_history(tick_loop_results)


# =============================================================================
# CONFIG FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def scenario_config() -> Dict[str, Any]:
    """Load raw partial close scenario config."""
    return load_scenario_config(PARTIAL_CLOSE_CONFIG)


@pytest.fixture(scope="session")
def trade_sequence(scenario_config: Dict[str, Any]) -> list:
    """Extract trade sequence from config."""
    return extract_trade_sequence(scenario_config)


@pytest.fixture(scope="session")
def partial_close_sequence(scenario_config: Dict[str, Any]) -> list:
    """Extract partial close sequence from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['partial_close_sequence']


@pytest.fixture(scope="session")
def seeds_config(scenario_config: Dict[str, Any]) -> Dict[str, int]:
    """Extract seeds from config."""
    return extract_seeds_config(scenario_config)


# =============================================================================
# DELAY GENERATOR FIXTURES (Function Scope)
# =============================================================================

@pytest.fixture(scope="function")
def api_delay_generator(seeds_config: Dict[str, int]) -> SeededDelayGenerator:
    """Fresh API delay generator with config seed."""
    return SeededDelayGenerator(
        seed=seeds_config['api_latency_seed'],
        min_delay=1,
        max_delay=3
    )


@pytest.fixture(scope="function")
def exec_delay_generator(seeds_config: Dict[str, int]) -> SeededDelayGenerator:
    """Fresh execution delay generator with config seed."""
    return SeededDelayGenerator(
        seed=seeds_config['market_execution_seed'],
        min_delay=2,
        max_delay=5
    )
