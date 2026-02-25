"""
FiniexTestingIDE - Margin Validation Test Fixtures
Suite-specific fixtures for backtesting/margin_validation_test.json

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
from python.framework.types.trading_env_stats_types import ExecutionStats
from python.framework.utils.seeded_generators.seeded_delay_generator import SeededDelayGenerator

from tests.shared.fixture_helpers import (
    run_scenario,
    extract_process_result,
    extract_tick_loop_results,
    extract_backtesting_metadata,
    extract_portfolio_stats,
    extract_trade_history,
    load_scenario_config,
    extract_seeds_config,
)

# =============================================================================
# CONFIG: Which scenario set does this suite run?
# =============================================================================
MARGIN_VALIDATION_CONFIG = "backtesting/margin_validation_test.json"


# =============================================================================
# SCENARIO EXECUTION (Session Scope)
# =============================================================================

@pytest.fixture(scope="session")
def batch_execution_summary() -> BatchExecutionSummary:
    """Execute margin validation backtesting scenario once per session."""
    return run_scenario(MARGIN_VALIDATION_CONFIG)


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


@pytest.fixture(scope="session")
def execution_stats(tick_loop_results: ProcessTickLoopResult) -> ExecutionStats:
    """Extract execution statistics."""
    return tick_loop_results.execution_stats


# =============================================================================
# CONFIG FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def scenario_config() -> Dict[str, Any]:
    """Load raw margin validation scenario config."""
    return load_scenario_config(MARGIN_VALIDATION_CONFIG)


@pytest.fixture(scope="session")
def trade_sequence(scenario_config: Dict[str, Any]) -> list:
    """Extract trade sequence from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['trade_sequence']


@pytest.fixture(scope="session")
def close_events(scenario_config: Dict[str, Any]) -> list:
    """Extract close events from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['close_events']


@pytest.fixture(scope="session")
def retry_events(scenario_config: Dict[str, Any]) -> list:
    """Extract retry events from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['retry_events']


@pytest.fixture(scope="session")
def edge_case_orders(scenario_config: Dict[str, Any]) -> list:
    """Extract edge case orders from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['edge_case_orders']


@pytest.fixture(scope="session")
def seeds_config(scenario_config: Dict[str, Any]) -> Dict[str, int]:
    """Extract seeds from config."""
    return extract_seeds_config(scenario_config)


# =============================================================================
# DERIVED FIXTURES (computed from config)
# =============================================================================

@pytest.fixture(scope="session")
def expected_successful_trades(trade_sequence: list, retry_events: list) -> int:
    """Count of trades expected to succeed (not rejected)."""
    successful_from_sequence = sum(
        1 for t in trade_sequence if not t.get('expect_rejection', False)
    )
    successful_retries = len(retry_events)
    return successful_from_sequence + successful_retries


@pytest.fixture(scope="session")
def expected_rejections(trade_sequence: list, edge_case_orders: list) -> int:
    """Count of expected order rejections (margin + lot validation)."""
    margin_rejections = sum(
        1 for t in trade_sequence if t.get('expect_rejection', False)
    )
    lot_rejections = sum(
        1 for e in edge_case_orders
        if e['type'] in ('invalid_lot_below_min', 'invalid_lot_above_max', 'invalid_lot_step')
    )
    return margin_rejections + lot_rejections


@pytest.fixture(scope="session")
def expected_orders_sent(
    trade_sequence: list,
    retry_events: list,
    edge_case_orders: list
) -> int:
    """Count of total orders sent (all open_order_with_latency calls)."""
    # All trade_sequence entries go through send_order
    from_sequence = len(trade_sequence)
    # All retry events go through send_order
    from_retries = len(retry_events)
    # Only lot validation edge cases go through send_order
    # (close_nonexistent goes through close_position, not send_order)
    from_edge_cases = sum(
        1 for e in edge_case_orders
        if e['type'] in ('invalid_lot_below_min', 'invalid_lot_above_max', 'invalid_lot_step')
    )
    return from_sequence + from_retries + from_edge_cases


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
