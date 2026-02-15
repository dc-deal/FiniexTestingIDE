"""
FiniexTestingIDE - Margin Validation Tests
Validates margin exhaustion, recovery, and execution statistics.

Tests:
- Margin exhaustion triggers INSUFFICIENT_MARGIN rejection
- Margin recovery after closing a position
- Execution statistics accuracy (sent, executed, rejected)
- Trade history contains only successful trades
"""

import pytest
from typing import Dict, Any, List

from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_trade_record_types import TradeRecord
from python.framework.types.process_data_types import ProcessTickLoopResult
from python.framework.types.trading_env_stats_types import ExecutionStats


class TestMarginExhaustion:
    """Validates that margin exhaustion produces correct rejections."""

    def test_has_rejected_orders(self, execution_stats: ExecutionStats):
        """Some orders should be rejected due to margin exhaustion."""
        assert execution_stats.orders_rejected > 0, (
            "Expected at least one rejected order from margin exhaustion"
        )

    def test_rejection_count_matches_expected(
        self,
        execution_stats: ExecutionStats,
        expected_rejections: int
    ):
        """Rejected order count should match expected rejections."""
        assert execution_stats.orders_rejected == expected_rejections, (
            f"Expected {expected_rejections} rejections, "
            f"got {execution_stats.orders_rejected}"
        )

    def test_no_position_created_after_rejection(
        self,
        trade_history: List[TradeRecord],
        expected_successful_trades: int
    ):
        """Trade history should only contain successfully opened trades."""
        assert len(trade_history) == expected_successful_trades, (
            f"Expected {expected_successful_trades} trades in history, "
            f"got {len(trade_history)}"
        )

    def test_successful_trades_count(
        self,
        portfolio_stats: PortfolioStats,
        expected_successful_trades: int
    ):
        """Portfolio total trades should match successful opens."""
        assert portfolio_stats.total_trades == expected_successful_trades, (
            f"Expected {expected_successful_trades} total trades, "
            f"got {portfolio_stats.total_trades}"
        )


class TestMarginRecovery:
    """Validates margin recovery after closing a position."""

    def test_retry_succeeded(
        self,
        backtesting_metadata: BacktestingMetadata,
        retry_events: list
    ):
        """Retry after margin recovery should produce a successful trade."""
        retry_trades = [
            t for t in backtesting_metadata.expected_trades
            if t.get('event_type') == 'retry'
        ]
        assert len(retry_trades) == len(retry_events), (
            f"Expected {len(retry_events)} retry trades, "
            f"got {len(retry_trades)}"
        )

    def test_retry_has_order_id(self, backtesting_metadata: BacktestingMetadata):
        """Successful retry should have an order_id assigned."""
        retry_trades = [
            t for t in backtesting_metadata.expected_trades
            if t.get('event_type') == 'retry'
        ]
        for trade in retry_trades:
            assert trade.get('order_id'), (
                f"Retry trade at tick {trade.get('signal_tick')} "
                f"missing order_id — order may have failed"
            )

    def test_total_trades_includes_retry(
        self,
        trade_history: List[TradeRecord],
        trade_sequence: list,
        retry_events: list
    ):
        """Trade history should include both initial successful trades and retries."""
        successful_initial = sum(
            1 for t in trade_sequence if not t.get('expect_rejection', False)
        )
        expected_total = successful_initial + len(retry_events)
        assert len(trade_history) == expected_total, (
            f"Expected {expected_total} total trades "
            f"({successful_initial} initial + {len(retry_events)} retries), "
            f"got {len(trade_history)}"
        )


class TestExecutionStatistics:
    """Validates execution statistics accuracy across all order types."""

    def test_orders_sent_count(
        self,
        execution_stats: ExecutionStats,
        expected_orders_sent: int
    ):
        """orders_sent should count all open order attempts."""
        assert execution_stats.orders_sent == expected_orders_sent, (
            f"Expected {expected_orders_sent} orders sent, "
            f"got {execution_stats.orders_sent}"
        )

    def test_orders_executed_count(
        self,
        execution_stats: ExecutionStats,
        expected_successful_trades: int
    ):
        """orders_executed should count only successfully opened positions."""
        assert execution_stats.orders_executed == expected_successful_trades, (
            f"Expected {expected_successful_trades} executed, "
            f"got {execution_stats.orders_executed}"
        )

    def test_sent_equals_executed_plus_rejected(
        self,
        execution_stats: ExecutionStats
    ):
        """orders_sent should equal orders_executed + orders_rejected."""
        total = execution_stats.orders_executed + execution_stats.orders_rejected
        assert execution_stats.orders_sent == total, (
            f"orders_sent ({execution_stats.orders_sent}) != "
            f"orders_executed ({execution_stats.orders_executed}) + "
            f"orders_rejected ({execution_stats.orders_rejected}) = {total}"
        )

    def test_trade_history_excludes_rejections(
        self,
        trade_history: List[TradeRecord],
        execution_stats: ExecutionStats
    ):
        """Trade history should not contain rejected orders."""
        assert len(trade_history) == execution_stats.orders_executed, (
            f"Trade history has {len(trade_history)} entries, "
            f"but {execution_stats.orders_executed} orders were executed"
        )
