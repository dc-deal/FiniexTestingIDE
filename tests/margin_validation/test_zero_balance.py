"""
FiniexTestingIDE - Zero Balance Order Rejection Tests
Validates that all orders are rejected when starting with zero balance.

Uses a separate scenario config (margin_validation_zero_balance_test.json)
with initial_balance=0 and leverage=500. Every order attempt must be
rejected with INSUFFICIENT_MARGIN since no margin is available.
"""

import pytest
from typing import Dict, Any, List

from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_trade_record_types import TradeRecord
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.trading_env_stats_types import ExecutionStats
from python.framework.types.batch_execution_types import BatchExecutionSummary

from tests.shared.fixture_helpers import (
    run_scenario,
    extract_process_result,
    extract_tick_loop_results,
    extract_backtesting_metadata,
    extract_portfolio_stats,
    extract_trade_history,
    load_scenario_config,
)


# =============================================================================
# CONFIG
# =============================================================================
ZERO_BALANCE_CONFIG = "backtesting/margin_validation_zero_balance_test.json"


# =============================================================================
# FIXTURES (self-contained, separate from conftest session fixtures)
# =============================================================================

@pytest.fixture(scope="module")
def zb_batch_summary() -> BatchExecutionSummary:
    """Execute zero-balance scenario once per module."""
    return run_scenario(ZERO_BALANCE_CONFIG)


@pytest.fixture(scope="module")
def zb_process_result(zb_batch_summary: BatchExecutionSummary) -> ProcessResult:
    """Extract first scenario ProcessResult."""
    return extract_process_result(zb_batch_summary)


@pytest.fixture(scope="module")
def zb_tick_loop_results(zb_process_result: ProcessResult) -> ProcessTickLoopResult:
    """Extract tick loop results."""
    return extract_tick_loop_results(zb_process_result)


@pytest.fixture(scope="module")
def zb_execution_stats(zb_tick_loop_results: ProcessTickLoopResult) -> ExecutionStats:
    """Extract execution statistics."""
    return zb_tick_loop_results.execution_stats


@pytest.fixture(scope="module")
def zb_trade_history(zb_tick_loop_results: ProcessTickLoopResult) -> List[TradeRecord]:
    """Extract trade history."""
    return extract_trade_history(zb_tick_loop_results)


@pytest.fixture(scope="module")
def zb_backtesting_metadata(
    zb_tick_loop_results: ProcessTickLoopResult
) -> BacktestingMetadata:
    """Extract backtesting metadata."""
    return extract_backtesting_metadata(zb_tick_loop_results)


@pytest.fixture(scope="module")
def zb_scenario_config() -> Dict[str, Any]:
    """Load raw zero-balance scenario config."""
    return load_scenario_config(ZERO_BALANCE_CONFIG)


@pytest.fixture(scope="module")
def zb_trade_sequence(zb_scenario_config: Dict[str, Any]) -> list:
    """Extract trade sequence from config."""
    return zb_scenario_config['global']['strategy_config'][
        'decision_logic_config']['trade_sequence']


# =============================================================================
# TESTS
# =============================================================================

class TestZeroBalanceRejection:
    """Validates that zero balance causes all orders to be rejected."""

    def test_scenario_completes(
        self,
        zb_backtesting_metadata: BacktestingMetadata,
        zb_scenario_config: Dict[str, Any]
    ):
        """Scenario should process all ticks despite all rejections."""
        expected_ticks = zb_scenario_config['scenarios'][0]['max_ticks']
        assert zb_backtesting_metadata.tick_count == expected_ticks, (
            f"Expected {expected_ticks} ticks, got {zb_backtesting_metadata.tick_count}. "
            f"Scenario may have crashed on rejection."
        )

    def test_all_orders_rejected(
        self,
        zb_execution_stats: ExecutionStats,
        zb_trade_sequence: list
    ):
        """Every order attempt should be rejected with zero balance."""
        expected_rejections = len(zb_trade_sequence)
        assert zb_execution_stats.orders_rejected == expected_rejections, (
            f"Expected {expected_rejections} rejections (all orders), "
            f"got {zb_execution_stats.orders_rejected}"
        )

    def test_no_orders_executed(
        self,
        zb_execution_stats: ExecutionStats
    ):
        """No orders should execute with zero balance."""
        assert zb_execution_stats.orders_executed == 0, (
            f"Expected 0 executed orders, got {zb_execution_stats.orders_executed}"
        )

    def test_no_trades_in_history(
        self,
        zb_trade_history: List[TradeRecord]
    ):
        """Trade history should be empty when all orders are rejected."""
        assert len(zb_trade_history) == 0, (
            f"Expected empty trade history, got {len(zb_trade_history)} trades"
        )

    def test_submitted_but_none_in_trade_history(
        self,
        zb_backtesting_metadata: BacktestingMetadata,
        zb_trade_history: List[TradeRecord],
        zb_trade_sequence: list
    ):
        """Orders are submitted (PENDING) but rejected at fill time due to margin.
        expected_trades tracks submissions, trade_history tracks fills."""
        assert len(zb_backtesting_metadata.expected_trades) == len(zb_trade_sequence), (
            f"Expected {len(zb_trade_sequence)} submitted orders in metadata, "
            f"got {len(zb_backtesting_metadata.expected_trades)}"
        )
        assert len(zb_trade_history) == 0, (
            f"No orders should have filled, but trade_history has "
            f"{len(zb_trade_history)} entries"
        )

    def test_orders_sent_equals_rejected(
        self,
        zb_execution_stats: ExecutionStats
    ):
        """All sent orders should equal rejected orders (none executed)."""
        assert zb_execution_stats.orders_sent == zb_execution_stats.orders_rejected, (
            f"orders_sent ({zb_execution_stats.orders_sent}) should equal "
            f"orders_rejected ({zb_execution_stats.orders_rejected}) "
            f"when all orders fail"
        )
