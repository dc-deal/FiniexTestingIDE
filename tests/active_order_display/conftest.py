"""
FiniexTestingIDE - Active Order Display Test Fixtures
Suite-specific fixtures for limit_stop_order_mock_scenario_test.json

Validates that active (unresolved) limit and stop orders are correctly
reported in pending_stats at scenario end.

Scenario design:
- Scenario 0 (active_limit_display): LONG LIMIT at 0.5000 — never fills (far below market)
- Scenario 1 (active_stop_display):  LONG STOP  at 5.0000 — never triggers (far above market)
- Both scenarios: 500 ticks, GBPUSD
"""

import pytest

from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.pending_order_stats_types import PendingOrderStats

from tests.shared.fixture_helpers import (
    run_scenario,
    extract_process_result,
    extract_tick_loop_results,
    extract_pending_stats,
)

# =============================================================================
# CONFIG: Which scenario set does this suite run?
# =============================================================================
ACTIVE_ORDER_CONFIG = "backtesting/limit_stop_order_mock_scenario_test.json"


# =============================================================================
# SCENARIO EXECUTION (Session Scope — runs once per test session)
# =============================================================================

@pytest.fixture(scope="session")
def batch_execution_summary() -> BatchExecutionSummary:
    """Execute active order display scenarios once per session."""
    return run_scenario(ACTIVE_ORDER_CONFIG)


# =============================================================================
# SCENARIO 0: active_limit_display
# =============================================================================

@pytest.fixture(scope="session")
def process_result_limit(batch_execution_summary: BatchExecutionSummary) -> ProcessResult:
    """Extract ProcessResult for scenario 0 (active_limit_display)."""
    return extract_process_result(batch_execution_summary, scenario_index=0)


@pytest.fixture(scope="session")
def tick_loop_results_limit(process_result_limit: ProcessResult) -> ProcessTickLoopResult:
    """Extract tick loop results for the limit scenario."""
    return extract_tick_loop_results(process_result_limit)


@pytest.fixture(scope="session")
def pending_stats_limit(tick_loop_results_limit: ProcessTickLoopResult) -> PendingOrderStats:
    """Extract pending stats for the limit scenario."""
    return extract_pending_stats(tick_loop_results_limit)


# =============================================================================
# SCENARIO 1: active_stop_display
# =============================================================================

@pytest.fixture(scope="session")
def process_result_stop(batch_execution_summary: BatchExecutionSummary) -> ProcessResult:
    """Extract ProcessResult for scenario 1 (active_stop_display)."""
    return extract_process_result(batch_execution_summary, scenario_index=1)


@pytest.fixture(scope="session")
def tick_loop_results_stop(process_result_stop: ProcessResult) -> ProcessTickLoopResult:
    """Extract tick loop results for the stop scenario."""
    return extract_tick_loop_results(process_result_stop)


@pytest.fixture(scope="session")
def pending_stats_stop(tick_loop_results_stop: ProcessTickLoopResult) -> PendingOrderStats:
    """Extract pending stats for the stop scenario."""
    return extract_pending_stats(tick_loop_results_stop)
