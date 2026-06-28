"""
FiniexTestingIDE - Trend Channel Reference Test Fixtures

Suite-specific fixtures for the didactic CORE/trend_channel_reference logic. Two small
mt5 EURUSD windows exercise the two entry modes end-to-end:
- limit_pullback → resting LIMIT entries
- stop_breakout  → resting STOP entries

Each run drives SL/TP at submission, the always-on trailing stop, the partial-close ladder,
and multi-position stacking. The tests assert these behaviors on the resulting trade history.
Fixtures live under tests/fixtures/scenario_sets/trend_channel_reference/ (§34).
"""

from pathlib import Path
from typing import List

import pytest

from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats

from tests.shared.fixture_helpers import (
    run_scenario,
    extract_process_result,
    extract_tick_loop_results,
    extract_trade_history,
    extract_execution_stats,
)

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / 'fixtures' / 'scenario_sets' / 'trend_channel_reference'
LIMIT_FIXTURE = str(_FIXTURE_DIR / 'trend_channel_reference_limit_fixture.json')
STOP_FIXTURE = str(_FIXTURE_DIR / 'trend_channel_reference_stop_fixture.json')


# =============================================================================
# SCENARIO EXECUTION (session scope — each fixture set runs once)
# =============================================================================

@pytest.fixture(scope="session")
def limit_batch() -> BatchExecutionSummary:
    """Execute the limit_pullback fixture once per session."""
    return run_scenario(LIMIT_FIXTURE)


@pytest.fixture(scope="session")
def stop_batch() -> BatchExecutionSummary:
    """Execute the stop_breakout fixture once per session."""
    return run_scenario(STOP_FIXTURE)


# =============================================================================
# DERIVED FIXTURES (process result / trade history / execution stats)
# =============================================================================

@pytest.fixture(scope="session")
def limit_process_result(limit_batch: BatchExecutionSummary) -> ProcessResult:
    """Process result for the limit_pullback window."""
    return extract_process_result(limit_batch, scenario_index=0)


@pytest.fixture(scope="session")
def stop_process_result(stop_batch: BatchExecutionSummary) -> ProcessResult:
    """Process result for the stop_breakout window."""
    return extract_process_result(stop_batch, scenario_index=0)


@pytest.fixture(scope="session")
def limit_trades(limit_process_result: ProcessResult) -> List[TradeRecord]:
    """Trade history for the limit_pullback window."""
    return extract_trade_history(extract_tick_loop_results(limit_process_result))


@pytest.fixture(scope="session")
def stop_trades(stop_process_result: ProcessResult) -> List[TradeRecord]:
    """Trade history for the stop_breakout window."""
    return extract_trade_history(extract_tick_loop_results(stop_process_result))


@pytest.fixture(scope="session")
def all_trades(limit_trades: List[TradeRecord], stop_trades: List[TradeRecord]) -> List[TradeRecord]:
    """Combined trade history across both entry modes."""
    return list(limit_trades) + list(stop_trades)


@pytest.fixture(scope="session")
def limit_execution_stats(limit_process_result: ProcessResult) -> ExecutionStats:
    """Execution stats for the limit_pullback window."""
    return extract_execution_stats(extract_tick_loop_results(limit_process_result))


@pytest.fixture(scope="session")
def stop_execution_stats(stop_process_result: ProcessResult) -> ExecutionStats:
    """Execution stats for the stop_breakout window."""
    return extract_execution_stats(extract_tick_loop_results(stop_process_result))
