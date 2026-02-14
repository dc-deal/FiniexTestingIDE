"""
FiniexTestingIDE - Trade Execution Tests
Validates trade execution matches expected sequence

Tests:
- Trade count matches expected
- Trade directions match config
- Orders executed without rejection
"""

import pytest
from typing import Dict, Any, List

from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.process_data_types import ProcessTickLoopResult


class TestTradeExecution:
    """Tests for trade execution validation."""

    def test_expected_trade_count(
        self,
        backtesting_metadata: BacktestingMetadata,
        trade_sequence: list
    ):
        """Expected trades should match config sequence length."""
        assert len(backtesting_metadata.expected_trades) == len(trade_sequence), (
            f"Expected {len(trade_sequence)} trades, "
            f"got {len(backtesting_metadata.expected_trades)}"
        )

    def test_executed_trade_count(
        self,
        portfolio_stats: PortfolioStats,
        trade_sequence: list
    ):
        """Executed trades should match expected count."""
        assert portfolio_stats.total_trades == len(trade_sequence), (
            f"Expected {len(trade_sequence)} executed trades, "
            f"got {portfolio_stats.total_trades}"
        )

    def test_no_rejected_orders(self, tick_loop_results: ProcessTickLoopResult):
        """All orders should be executed without rejection."""
        exec_stats = tick_loop_results.execution_stats
        assert exec_stats.orders_rejected == 0, (
            f"Orders rejected: {exec_stats.orders_rejected}"
        )

    def test_orders_sent_equals_executed(self, tick_loop_results: ProcessTickLoopResult):
        """All sent orders should be executed."""
        exec_stats = tick_loop_results.execution_stats
        assert exec_stats.orders_sent == exec_stats.orders_executed, (
            f"Sent: {exec_stats.orders_sent}, Executed: {exec_stats.orders_executed}"
        )

    def test_trade_directions_match(
        self,
        backtesting_metadata: BacktestingMetadata,
        trade_sequence: list
    ):
        """Trade directions should match config sequence."""
        for i, (expected, actual) in enumerate(zip(
            trade_sequence,
            backtesting_metadata.expected_trades
        )):
            assert expected['direction'] == actual['direction'], (
                f"Trade {i}: expected {expected['direction']}, "
                f"got {actual['direction']}"
            )

    def test_trade_signal_ticks_match(
        self,
        backtesting_metadata: BacktestingMetadata,
        trade_sequence: list
    ):
        """Trade signal ticks should match config sequence."""
        for i, (expected, actual) in enumerate(zip(
            trade_sequence,
            backtesting_metadata.expected_trades
        )):
            assert expected['tick_number'] == actual['signal_tick'], (
                f"Trade {i}: expected tick {expected['tick_number']}, "
                f"got {actual['signal_tick']}"
            )

    def test_long_short_distribution(
        self,
        portfolio_stats: PortfolioStats,
        trade_sequence: list
    ):
        """Long/short distribution should match config."""
        expected_long = sum(
            1 for t in trade_sequence if t['direction'] == 'LONG')
        expected_short = sum(
            1 for t in trade_sequence if t['direction'] == 'SHORT')

        assert portfolio_stats.total_long_trades == expected_long, (
            f"Expected {expected_long} long trades, got {portfolio_stats.total_long_trades}"
        )
        assert portfolio_stats.total_short_trades == expected_short, (
            f"Expected {expected_short} short trades, got {portfolio_stats.total_short_trades}"
        )
