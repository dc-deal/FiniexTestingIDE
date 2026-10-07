"""
FiniexTestingIDE - Pending Stats Test Fixtures
Suite-specific fixtures for pending_stats_validation_test.json

Tests the pending-order counters the scenario's order-event stream yields (#362):
- The counters add up to the submissions
- Answer durations populated
- An order the data's end met on its way counts as expired

Config design:
- Trade 1: Opens at tick 10, closes at tick 110 (normal happy path)
- Trade 2: Opens at tick 5000 (last tick) — no subsequent tick to deliver it, expired on its way
- Max ticks: 5000
- Seeds: inbound_latency=12345
"""

from typing import Any, Dict, List

import pytest

from python.framework.types.probe_metadata_types import ProbeMetadata
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.reporting.builders.pending_orders_report_builder import pending_orders_row
from python.framework.types.api.report_types import PendingOrdersUnitRow
from tests.shared.fixture_helpers import (
    extract_probe_metadata,
    extract_portfolio_stats,
    extract_process_result,
    extract_tick_loop_results,
    extract_trade_history,
    extract_trade_sequence,
    load_scenario_config,
    run_scenario,
)

# =============================================================================
# CONFIG: Which scenario set does this suite run?
# =============================================================================
PENDING_STATS_CONFIG = 'backtesting/pending_stats_validation_test.json'


# =============================================================================
# SCENARIO EXECUTION (Session Scope — runs once per test session)
# =============================================================================

@pytest.fixture(scope='session')
def batch_execution_summary() -> BatchExecutionSummary:
    """Execute pending stats scenario once per session."""
    return run_scenario(PENDING_STATS_CONFIG)


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
def pending_row(
    process_result: ProcessResult, tick_loop_results: ProcessTickLoopResult
) -> PendingOrdersUnitRow:
    """The scenario's pending-order row, derived from its order-event stream."""
    # The symbol labels the row and is not asserted — every counter comes from the events
    return pending_orders_row(
        process_result.scenario_name, '', tick_loop_results.order_events or [],
        tick_loop_results.active_orders)


# =============================================================================
# CONFIG FIXTURES (raw JSON access for assertions)
# =============================================================================

@pytest.fixture(scope='session')
def scenario_config() -> Dict[str, Any]:
    """Load raw scenario config."""
    return load_scenario_config(PENDING_STATS_CONFIG)


@pytest.fixture(scope='session')
def trade_sequence(scenario_config: Dict[str, Any]) -> list:
    """Extract trade sequence from config."""
    return extract_trade_sequence(scenario_config)
