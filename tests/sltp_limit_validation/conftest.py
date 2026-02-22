"""
FiniexTestingIDE - SL/TP & Limit Order Validation Test Fixtures
Suite-specific fixtures for sltp_limit_validation_test.json

Tests SL/TP trigger detection, limit order fills, and modifications:
- Scenarios 0-4: SL/TP triggers (LONG/SHORT) + position modify
- Scenarios 5-8: Limit order fills (LONG/SHORT), limit+SL/TP combo, modify limit
- Scenarios 9-15: Stop/Stop-Limit orders, stop+TP combo, modify stop, cancel stop

Config design:
- 16 scenarios using USDJPY extreme move time windows
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


# --- Scenario 9: stop_long_trigger ---

@pytest.fixture(scope="session")
def stop_long_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for STOP LONG trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=9)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def stop_long_trade_history(stop_long_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for STOP LONG trigger scenario."""
    return extract_trade_history(stop_long_tick_loop)


@pytest.fixture(scope="session")
def stop_long_execution_stats(stop_long_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for STOP LONG trigger scenario."""
    return extract_execution_stats(stop_long_tick_loop)


# --- Scenario 10: stop_short_trigger ---

@pytest.fixture(scope="session")
def stop_short_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for STOP SHORT trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=10)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def stop_short_trade_history(stop_short_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for STOP SHORT trigger scenario."""
    return extract_trade_history(stop_short_tick_loop)


@pytest.fixture(scope="session")
def stop_short_execution_stats(stop_short_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for STOP SHORT trigger scenario."""
    return extract_execution_stats(stop_short_tick_loop)


# --- Scenario 11: stop_limit_long_trigger ---

@pytest.fixture(scope="session")
def stop_limit_long_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for STOP_LIMIT LONG trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=11)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def stop_limit_long_trade_history(stop_limit_long_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for STOP_LIMIT LONG trigger scenario."""
    return extract_trade_history(stop_limit_long_tick_loop)


@pytest.fixture(scope="session")
def stop_limit_long_execution_stats(stop_limit_long_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for STOP_LIMIT LONG trigger scenario."""
    return extract_execution_stats(stop_limit_long_tick_loop)


# --- Scenario 12: stop_limit_short_trigger ---

@pytest.fixture(scope="session")
def stop_limit_short_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for STOP_LIMIT SHORT trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=12)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def stop_limit_short_trade_history(stop_limit_short_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for STOP_LIMIT SHORT trigger scenario."""
    return extract_trade_history(stop_limit_short_tick_loop)


@pytest.fixture(scope="session")
def stop_limit_short_execution_stats(stop_limit_short_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for STOP_LIMIT SHORT trigger scenario."""
    return extract_execution_stats(stop_limit_short_tick_loop)


# --- Scenario 13: stop_long_then_tp ---

@pytest.fixture(scope="session")
def stop_tp_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for STOP LONG then TP trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=13)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def stop_tp_trade_history(stop_tp_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for STOP LONG then TP trigger scenario."""
    return extract_trade_history(stop_tp_tick_loop)


@pytest.fixture(scope="session")
def stop_tp_execution_stats(stop_tp_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for STOP LONG then TP trigger scenario."""
    return extract_execution_stats(stop_tp_tick_loop)


# --- Scenario 14: modify_stop_trigger ---

@pytest.fixture(scope="session")
def modify_stop_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for modify stop trigger scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=14)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def modify_stop_trade_history(modify_stop_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for modify stop trigger scenario."""
    return extract_trade_history(modify_stop_tick_loop)


@pytest.fixture(scope="session")
def modify_stop_execution_stats(modify_stop_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for modify stop trigger scenario."""
    return extract_execution_stats(modify_stop_tick_loop)


# --- Scenario 15: cancel_stop_no_fill ---

@pytest.fixture(scope="session")
def cancel_stop_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for cancel stop no fill scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=15)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def cancel_stop_trade_history(cancel_stop_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for cancel stop no fill scenario."""
    return extract_trade_history(cancel_stop_tick_loop)


@pytest.fixture(scope="session")
def cancel_stop_execution_stats(cancel_stop_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for cancel stop no fill scenario."""
    return extract_execution_stats(cancel_stop_tick_loop)


# --- Scenario 16: cancel_limit_no_fill ---

@pytest.fixture(scope="session")
def cancel_limit_tick_loop(batch_execution_summary: BatchExecutionSummary) -> ProcessTickLoopResult:
    """Tick loop results for cancel limit no fill scenario."""
    pr = extract_process_result(batch_execution_summary, scenario_index=16)
    return extract_tick_loop_results(pr)


@pytest.fixture(scope="session")
def cancel_limit_trade_history(cancel_limit_tick_loop: ProcessTickLoopResult) -> List[TradeRecord]:
    """Trade history for cancel limit no fill scenario."""
    return extract_trade_history(cancel_limit_tick_loop)


@pytest.fixture(scope="session")
def cancel_limit_execution_stats(cancel_limit_tick_loop: ProcessTickLoopResult) -> ExecutionStats:
    """Execution stats for cancel limit no fill scenario."""
    return extract_execution_stats(cancel_limit_tick_loop)
