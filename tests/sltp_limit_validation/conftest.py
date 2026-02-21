"""
FiniexTestingIDE - SL/TP & Limit Order Validation Test Fixtures
Suite-specific fixtures for sltp_limit_validation_test.json

Tests SL/TP trigger detection, limit order fills, and modifications:
- Scenarios 0-4: SL/TP triggers (LONG/SHORT) + position modify
- Scenarios 5-8: Limit order fills (LONG/SHORT), limit+SL/TP combo, modify limit

Config design:
- 9 scenarios using USDJPY extreme move time windows
- Each scenario opens 1 trade at tick 10
- hold_ticks=999999 ensures SL/TP triggers before hold expiry
- Seeds: api_latency=12345, market_execution=67890
"""

import pytest
from typing import List

from python.framework.types.portfolio_trade_record_types import TradeRecord
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.trading_env_stats_types import ExecutionStats

from tests.shared.fixture_helpers import (
    run_scenario,
    extract_process_result,
    extract_tick_loop_results,
    extract_trade_history,
    extract_execution_stats,
)

# =============================================================================
# CONFIG: Which scenario set does this suite run?
# =============================================================================
SLTP_LIMIT_VALIDATION_CONFIG = "backtesting/sltp_limit_validation_test.json"


# =============================================================================
# SCENARIO EXECUTION (Session Scope — runs once per test session)
# =============================================================================

@pytest.fixture(scope="session")
def batch_execution_summary() -> BatchExecutionSummary:
    """Execute SL/TP & limit order validation scenarios once per session."""
    return run_scenario(SLTP_LIMIT_VALIDATION_CONFIG)


# =============================================================================
# PER-SCENARIO FIXTURES (index matches scenario order in config)
# =============================================================================

# --- Scenario 0: long_tp_trigger ---

@pytest.fixture(scope="session")
def long_tp_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for LONG TP trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=0)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def long_tp_trade_history(long_tp_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for LONG TP trigger scenario."""
    return extract_trade_history(long_tp_tick_loop)


@pytest.fixture(scope="session")
def long_tp_execution_stats(long_tp_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for LONG TP trigger scenario."""
    return extract_execution_stats(long_tp_tick_loop)


# --- Scenario 1: long_sl_trigger ---

@pytest.fixture(scope="session")
def long_sl_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for LONG SL trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=1)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def long_sl_trade_history(long_sl_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for LONG SL trigger scenario."""
    return extract_trade_history(long_sl_tick_loop)


@pytest.fixture(scope="session")
def long_sl_execution_stats(long_sl_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for LONG SL trigger scenario."""
    return extract_execution_stats(long_sl_tick_loop)


# --- Scenario 2: short_tp_trigger ---

@pytest.fixture(scope="session")
def short_tp_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for SHORT TP trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=2)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def short_tp_trade_history(short_tp_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for SHORT TP trigger scenario."""
    return extract_trade_history(short_tp_tick_loop)


@pytest.fixture(scope="session")
def short_tp_execution_stats(short_tp_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for SHORT TP trigger scenario."""
    return extract_execution_stats(short_tp_tick_loop)


# --- Scenario 3: short_sl_trigger ---

@pytest.fixture(scope="session")
def short_sl_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for SHORT SL trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=3)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def short_sl_trade_history(short_sl_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for SHORT SL trigger scenario."""
    return extract_trade_history(short_sl_tick_loop)


@pytest.fixture(scope="session")
def short_sl_execution_stats(short_sl_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for SHORT SL trigger scenario."""
    return extract_execution_stats(short_sl_tick_loop)


# --- Scenario 4: modify_tp_trigger ---

@pytest.fixture(scope="session")
def modify_tp_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for modify TP trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=4)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def modify_tp_trade_history(modify_tp_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for modify TP trigger scenario."""
    return extract_trade_history(modify_tp_tick_loop)


@pytest.fixture(scope="session")
def modify_tp_execution_stats(modify_tp_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for modify TP trigger scenario."""
    return extract_execution_stats(modify_tp_tick_loop)


# --- Scenario 5: long_limit_fill ---

@pytest.fixture(scope="session")
def long_limit_fill_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for LONG limit fill scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=5)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def long_limit_fill_trade_history(long_limit_fill_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for LONG limit fill scenario."""
    return extract_trade_history(long_limit_fill_tick_loop)


@pytest.fixture(scope="session")
def long_limit_fill_execution_stats(long_limit_fill_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for LONG limit fill scenario."""
    return extract_execution_stats(long_limit_fill_tick_loop)


# --- Scenario 6: short_limit_fill ---

@pytest.fixture(scope="session")
def short_limit_fill_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for SHORT limit fill scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=6)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def short_limit_fill_trade_history(short_limit_fill_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for SHORT limit fill scenario."""
    return extract_trade_history(short_limit_fill_tick_loop)


@pytest.fixture(scope="session")
def short_limit_fill_execution_stats(short_limit_fill_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for SHORT limit fill scenario."""
    return extract_execution_stats(short_limit_fill_tick_loop)


# --- Scenario 7: limit_fill_then_sl ---

@pytest.fixture(scope="session")
def limit_sl_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for limit fill then SL trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=7)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def limit_sl_trade_history(limit_sl_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for limit fill then SL trigger scenario."""
    return extract_trade_history(limit_sl_tick_loop)


@pytest.fixture(scope="session")
def limit_sl_execution_stats(limit_sl_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for limit fill then SL trigger scenario."""
    return extract_execution_stats(limit_sl_tick_loop)


# --- Scenario 8: modify_limit_price_fill ---

@pytest.fixture(scope="session")
def modify_limit_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for modify limit price fill scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=8)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def modify_limit_trade_history(modify_limit_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for modify limit price fill scenario."""
    return extract_trade_history(modify_limit_tick_loop)


@pytest.fixture(scope="session")
def modify_limit_execution_stats(modify_limit_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for modify limit price fill scenario."""
    return extract_execution_stats(modify_limit_tick_loop)
