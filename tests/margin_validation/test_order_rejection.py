"""
FiniexTestingIDE - Order Rejection & Edge Case Tests
Validates that invalid operations are rejected cleanly.

Tests:
- Lot size validation (below min, above max)
- Close non-existent position (no crash)
- Rejection tracking in execution statistics
- Rejected orders absent from trade history
"""

import pytest
from typing import Dict, Any, List

from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_trade_record_types import TradeRecord
from python.framework.types.process_data_types import ProcessTickLoopResult
from python.framework.types.trading_env_stats_types import ExecutionStats


class TestLotSizeValidation:
    """Validates that invalid lot sizes are rejected immediately."""

    def test_lot_validation_rejections_counted(
        self,
        execution_stats: ExecutionStats,
        edge_case_orders: list
    ):
        """Lot validation rejections should be included in orders_rejected."""
        lot_edge_cases = sum(
            1 for e in edge_case_orders
            if e['type'] in ('invalid_lot_below_min', 'invalid_lot_above_max', 'invalid_lot_step')
        )
        # orders_rejected includes both margin and lot rejections
        assert execution_stats.orders_rejected >= lot_edge_cases, (
            f"Expected at least {lot_edge_cases} rejections from lot validation, "
            f"orders_rejected={execution_stats.orders_rejected}"
        )

    def test_invalid_lots_not_in_trade_history(
        self,
        trade_history: List[TradeRecord],
        edge_case_orders: list
    ):
        """Trades with invalid lot sizes should not appear in trade history."""
        invalid_lots = {
            e['lot_size'] for e in edge_case_orders
            if e['type'] in ('invalid_lot_below_min', 'invalid_lot_above_max')
        }
        for trade in trade_history:
            assert trade.lots not in invalid_lots, (
                f"Trade with lots={trade.lots} should not be in history "
                f"(matches invalid lot size)"
            )

    def test_invalid_lots_not_in_expected_trades(
        self,
        backtesting_metadata: BacktestingMetadata,
        edge_case_orders: list
    ):
        """Expected trades should not contain rejected lot validation orders."""
        invalid_lots = {
            e['lot_size'] for e in edge_case_orders
            if e['type'] in ('invalid_lot_below_min', 'invalid_lot_above_max')
        }
        for trade in backtesting_metadata.expected_trades:
            assert trade.get('lot_size') not in invalid_lots, (
                f"Expected trade with lots={trade.get('lot_size')} "
                f"should not exist (invalid lot)"
            )


class TestPositionCloseErrors:
    """Validates that closing non-existent positions doesn't crash."""

    def test_scenario_completes_despite_close_error(
        self,
        backtesting_metadata: BacktestingMetadata,
        scenario_config: Dict[str, Any]
    ):
        """Scenario should complete all ticks despite close errors."""
        expected_ticks = scenario_config['scenarios'][0]['max_ticks']
        assert backtesting_metadata.tick_count == expected_ticks, (
            f"Expected {expected_ticks} ticks, got {backtesting_metadata.tick_count}. "
            f"Scenario may have crashed on close error."
        )

    def test_successful_trades_unaffected_by_close_error(
        self,
        trade_history: List[TradeRecord],
        expected_successful_trades: int
    ):
        """Successful trades should be unaffected by close errors."""
        assert len(trade_history) == expected_successful_trades, (
            f"Expected {expected_successful_trades} trades despite close errors, "
            f"got {len(trade_history)}"
        )


class TestRejectionTracking:
    """Validates that all rejection types are correctly tracked."""

    def test_orders_sent_includes_rejected(
        self,
        execution_stats: ExecutionStats
    ):
        """orders_sent should include rejected orders in the count."""
        assert execution_stats.orders_sent > execution_stats.orders_executed, (
            f"orders_sent ({execution_stats.orders_sent}) should be > "
            f"orders_executed ({execution_stats.orders_executed}) "
            f"when rejections occur"
        )

    def test_rejected_orders_not_in_trade_history(
        self,
        trade_history: List[TradeRecord],
        execution_stats: ExecutionStats
    ):
        """Rejected orders should not appear in trade history."""
        assert len(trade_history) == execution_stats.orders_executed, (
            f"Trade history ({len(trade_history)}) should match "
            f"orders_executed ({execution_stats.orders_executed}), "
            f"not orders_sent ({execution_stats.orders_sent})"
        )

    def test_all_rejections_accounted_for(
        self,
        execution_stats: ExecutionStats,
        expected_rejections: int
    ):
        """Total rejections should match expected count."""
        assert execution_stats.orders_rejected == expected_rejections, (
            f"Expected {expected_rejections} total rejections, "
            f"got {execution_stats.orders_rejected}"
        )
