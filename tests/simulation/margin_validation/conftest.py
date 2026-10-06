"""
FiniexTestingIDE - Margin Validation Test Fixtures
Suite-specific fixtures for backtesting/margin_validation_test.json

All extraction logic lives in tests/shared/fixture_helpers.py.
This conftest only wires the config path and creates pytest fixtures.
"""

from typing import Any, Dict, List

import pytest

from python.framework.types.probe_metadata_types import ProbeMetadata
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats
from python.framework.utils.seeded_generators.seeded_delay_generator import SeededDelayGenerator
from tests.shared.fixture_helpers import (
    extract_probe_metadata,
    extract_portfolio_stats,
    extract_process_result,
    extract_seeds_config,
    extract_tick_loop_results,
    extract_trade_history,
    load_scenario_config,
    run_scenario,
)

# =============================================================================
# CONFIG: Which scenario set does this suite run?
# =============================================================================
MARGIN_VALIDATION_CONFIG = 'backtesting/margin_validation_test.json'


# =============================================================================
# SCENARIO EXECUTION (Session Scope)
# =============================================================================

@pytest.fixture(scope='session')
def batch_execution_summary() -> BatchExecutionSummary:
    """Execute margin validation backtesting scenario once per session."""
    return run_scenario(MARGIN_VALIDATION_CONFIG)


@pytest.fixture(scope='session')
def process_result(batch_execution_summary: BatchExecutionSummary) -> ProcessResult:
    """Extract first scenario ProcessResult."""
    return extract_process_result(batch_execution_summary)


@pytest.fixture(scope='session')
def tick_loop_results(process_result: ProcessResult) -> ProcessTickLoopResult:
    """Extract tick loop results."""
    return extract_tick_loop_results(process_result)


@pytest.fixture(scope='session')
def probe_metadata(tick_loop_results: ProcessTickLoopResult) -> ProbeMetadata:
    """Extract ProbeMetadata from decision statistics."""
    return extract_probe_metadata(tick_loop_results)


@pytest.fixture(scope='session')
def portfolio_stats(tick_loop_results: ProcessTickLoopResult) -> PortfolioStats:
    """Extract portfolio statistics."""
    return extract_portfolio_stats(tick_loop_results)


@pytest.fixture(scope='session')
def trade_history(tick_loop_results: ProcessTickLoopResult) -> List[TradeRecord]:
    """Extract trade history for P&L verification."""
    return extract_trade_history(tick_loop_results)


@pytest.fixture(scope='session')
def execution_stats(tick_loop_results: ProcessTickLoopResult) -> ExecutionStats:
    """Extract execution statistics."""
    return tick_loop_results.execution_stats


# =============================================================================
# CONFIG FIXTURES
# =============================================================================

@pytest.fixture(scope='session')
def scenario_config() -> Dict[str, Any]:
    """Load raw margin validation scenario config."""
    return load_scenario_config(MARGIN_VALIDATION_CONFIG)


@pytest.fixture(scope='session')
def trade_sequence(scenario_config: Dict[str, Any]) -> list:
    """Extract trade sequence from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['trade_sequence']


@pytest.fixture(scope='session')
def close_events(scenario_config: Dict[str, Any]) -> list:
    """Extract close events from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['close_events']


@pytest.fixture(scope='session')
def retry_events(scenario_config: Dict[str, Any]) -> list:
    """Extract retry events from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['retry_events']


@pytest.fixture(scope='session')
def edge_case_orders(scenario_config: Dict[str, Any]) -> list:
    """Extract edge case orders from config."""
    return scenario_config['global']['strategy_config'][
        'decision_logic_config']['edge_case_orders']


@pytest.fixture(scope='session')
def seeds_config(scenario_config: Dict[str, Any]) -> Dict[str, int]:
    """Extract seeds from config."""
    return extract_seeds_config(scenario_config)


# =============================================================================
# DERIVED FIXTURES (computed from config)
# =============================================================================

@pytest.fixture(scope='session')
def expected_successful_trades(trade_sequence: list, retry_events: list) -> int:
    """Count of trades expected to succeed (not rejected)."""
    successful_from_sequence = sum(
        1 for t in trade_sequence if not t.get('expect_rejection', False)
    )
    successful_retries = len(retry_events)
    return successful_from_sequence + successful_retries


@pytest.fixture(scope='session')
def expected_rejections(trade_sequence: list) -> int:
    """
    Count of expected rejections — the simulated venue's margin check at the fill.

    A lot-validation refusal is no rejection: the executor refuses it before anything is
    sent, so it is denied (#362).
    """
    return sum(1 for t in trade_sequence if t.get('expect_rejection', False))


@pytest.fixture(scope='session')
def expected_denials(edge_case_orders: list) -> int:
    """
    Count of expected denials — the lot edge cases, and the close of a missing position.

    All refused before anything is sent; the close of a missing position used to leave no row
    and no count at all.
    """
    return sum(
        1 for e in edge_case_orders
        if e['type'] in ('invalid_lot_below_min', 'invalid_lot_above_max', 'invalid_lot_step',
                         'close_nonexistent')
    )


@pytest.fixture(scope='session')
def expected_orders_submitted(
    trade_sequence: list,
    retry_events: list,
    trade_history: List[TradeRecord],
) -> int:
    """
    Count of the orders handed to the simulated venue — opens and closes.

    Every trade_sequence entry and every retry passes the submission checks (the margin check
    refuses at the FILL); every close that went out produced one trade record.
    """
    return len(trade_sequence) + len(retry_events) + len(trade_history)


# =============================================================================
# DELAY GENERATOR FIXTURES (Function Scope)
# =============================================================================

@pytest.fixture(scope='function')
def inbound_delay_generator(seeds_config: Dict[str, int]) -> SeededDelayGenerator:
    """Fresh Inbound delay generator with config seed (ms-based)."""
    return SeededDelayGenerator(
        seed=seeds_config['inbound_latency_seed'],
        min_delay=20,
        max_delay=80
    )


