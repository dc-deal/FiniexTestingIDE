"""
FiniexTestingIDE - Pending Stats Test Fixtures
Suite-specific fixtures for pending_stats_validation_test.json

Tests pending order statistics:
- Synthetic close path (no false force-closed)
- Real force-closed detection (stuck-in-pipeline)
- Latency stats population
- Anomaly records with reason

Config design:
- Trade 1: Opens at tick 10, closes at tick 110 (normal happy path)
- Trade 2: Opens at tick 4990, close signal at tick 4993 (stuck in pipeline at scenario end)
- Max ticks: 5000
- Seeds: api_latency=12345, market_execution=67890 (~3-8 tick latency)
"""

import pytest
from typing import Dict, Any, List

from python.framework.types.portfolio_trade_record_types import TradeRecord
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.pending_order_stats_types import PendingOrderStats

from tests.shared.fixture_helpers import (
    run_scenario,
    extract_process_result,
    extract_tick_loop_results,
    extract_backtesting_metadata,
    extract_portfolio_stats,
    extract_trade_history,
    extract_pending_stats,
    load_scenario_config,
    extract_trade_sequence,
)

# =============================================================================
# CONFIG: Which scenario set does this suite run?
# =============================================================================
PENDING_STATS_CONFIG = "backtesting/pending_stats_validation_test.json"


# =============================================================================
# SCENARIO EXECUTION (Session Scope — runs once per test session)
# =============================================================================

@pytest.fixture(scope="session")
def batch_execution_summary() -> BatchExecutionSummary:
    """Execute pending stats scenario once per session."""
    return run_scenario(PENDING_STATS_CONFIG)


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
def pending_stats(tick_loop_results: ProcessTickLoopResult) -> PendingOrderStats:
    """Extract pending order statistics."""
    return extract_pending_stats(tick_loop_results)


# =============================================================================
# CONFIG FIXTURES (raw JSON access for assertions)
# =============================================================================

@pytest.fixture(scope="session")
def scenario_config() -> Dict[str, Any]:
    """Load raw scenario config."""
    return load_scenario_config(PENDING_STATS_CONFIG)


@pytest.fixture(scope="session")
def trade_sequence(scenario_config: Dict[str, Any]) -> list:
    """Extract trade sequence from config."""
    return extract_trade_sequence(scenario_config)
