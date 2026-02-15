"""
FiniexTestingIDE - Shared P&L Calculation Tests
Reusable test classes for P&L validation across test suites.

Used by: mvp_baseline, multi_position, margin_validation
Import these classes into suite-specific test_pnl_calculation.py files.
"""

import pytest
from typing import List

from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_trade_record_types import TradeRecord


class TestPnLCalculation:
    """Tests for P&L calculation validation using trade_history."""

    def test_trade_count_matches(
        self,
        trade_history: List[TradeRecord],
        portfolio_stats: PortfolioStats
    ):
        """Trade history count should match portfolio total trades."""
        assert len(trade_history) == portfolio_stats.total_trades, (
            f"Trade history has {len(trade_history)} trades, "
            f"portfolio reports {portfolio_stats.total_trades}"
        )

    def test_total_pnl_matches_portfolio(
        self,
        trade_history: List[TradeRecord],
        portfolio_stats: PortfolioStats
    ):
        """Sum of trade net_pnl should match portfolio P&L."""
        expected_total = sum(t.net_pnl for t in trade_history)
        actual_total = portfolio_stats.total_profit - portfolio_stats.total_loss

        tolerance = 0.01
        assert abs(expected_total - actual_total) < tolerance, (
            f"Trade history total: {expected_total:.4f}, "
            f"Portfolio P&L: {actual_total:.4f}, "
            f"Diff: {abs(expected_total - actual_total):.6f}"
        )

    def test_total_spread_cost_matches(
        self,
        trade_history: List[TradeRecord],
        portfolio_stats: PortfolioStats
    ):
        """Sum of spread costs should match portfolio spread cost."""
        expected_spread = sum(t.spread_cost for t in trade_history)
        actual_spread = portfolio_stats.total_spread_cost

        tolerance = 0.01
        assert abs(expected_spread - actual_spread) < tolerance, (
            f"Trade history spread: {expected_spread:.4f}, "
            f"Portfolio spread: {actual_spread:.4f}"
        )

    def test_net_pnl_formula(self, trade_history: List[TradeRecord]):
        """Net P&L should equal gross P&L minus total fees."""
        for i, trade in enumerate(trade_history):
            expected_net = trade.gross_pnl - trade.total_fees
            tolerance = 0.001

            assert abs(trade.net_pnl - expected_net) < tolerance, (
                f"Trade {i+1}: net_pnl={trade.net_pnl:.4f}, "
                f"expected={expected_net:.4f} (gross={trade.gross_pnl:.4f} - fees={trade.total_fees:.4f})"
            )

    def test_total_fees_breakdown(self, trade_history: List[TradeRecord]):
        """Total fees should equal sum of fee components."""
        for i, trade in enumerate(trade_history):
            expected_fees = trade.spread_cost + trade.commission_cost + trade.swap_cost
            tolerance = 0.001

            assert abs(trade.total_fees - expected_fees) < tolerance, (
                f"Trade {i+1}: total_fees={trade.total_fees:.4f}, "
                f"components sum={expected_fees:.4f}"
            )

    def test_gross_pnl_formula(self, trade_history: List[TradeRecord]):
        """Gross P&L should follow standard formula."""
        for i, trade in enumerate(trade_history):
            # Calculate expected gross P&L
            if trade.direction == "LONG":
                price_diff = trade.exit_price - trade.entry_price
            else:
                price_diff = trade.entry_price - trade.exit_price

            points = price_diff * (10 ** trade.digits)
            expected_gross = points * trade.entry_tick_value * trade.lots

            tolerance = 0.01
            assert abs(trade.gross_pnl - expected_gross) < tolerance, (
                f"Trade {i+1}: gross_pnl={trade.gross_pnl:.4f}, "
                f"expected={expected_gross:.4f} "
                f"(diff={price_diff:.5f}, points={points:.2f}, tv={trade.entry_tick_value}, lots={trade.lots})"
            )

    def test_exit_after_entry(self, trade_history: List[TradeRecord]):
        """Exit tick should be after entry tick."""
        for i, trade in enumerate(trade_history):
            assert trade.exit_tick_index > trade.entry_tick_index, (
                f"Trade {i+1}: exit tick {trade.exit_tick_index} "
                f"not after entry tick {trade.entry_tick_index}"
            )

    def test_positive_lots(self, trade_history: List[TradeRecord]):
        """Lot size should be positive."""
        for i, trade in enumerate(trade_history):
            assert trade.lots > 0, f"Trade {i+1}: lots {trade.lots} not positive"

    def test_spread_cost_positive(self, trade_history: List[TradeRecord]):
        """Spread cost should be positive (it's a cost)."""
        for i, trade in enumerate(trade_history):
            assert trade.spread_cost >= 0, (
                f"Trade {i+1}: spread_cost {trade.spread_cost} is negative"
            )

    def test_winning_losing_count(
        self,
        trade_history: List[TradeRecord],
        portfolio_stats: PortfolioStats
    ):
        """Winning/losing trade counts should match."""
        expected_winners = sum(1 for t in trade_history if t.net_pnl > 0)
        expected_losers = sum(1 for t in trade_history if t.net_pnl <= 0)

        assert expected_winners == portfolio_stats.winning_trades, (
            f"Expected {expected_winners} winners, got {portfolio_stats.winning_trades}"
        )
        assert expected_losers == portfolio_stats.losing_trades, (
            f"Expected {expected_losers} losers, got {portfolio_stats.losing_trades}"
        )

    def test_direction_counts(
        self,
        trade_history: List[TradeRecord],
        portfolio_stats: PortfolioStats
    ):
        """Long/short trade counts should match."""
        expected_long = sum(1 for t in trade_history if t.direction == "LONG")
        expected_short = sum(
            1 for t in trade_history if t.direction == "SHORT")

        assert expected_long == portfolio_stats.total_long_trades, (
            f"Expected {expected_long} long, got {portfolio_stats.total_long_trades}"
        )
        assert expected_short == portfolio_stats.total_short_trades, (
            f"Expected {expected_short} short, got {portfolio_stats.total_short_trades}"
        )

    def test_valid_prices(self, trade_history: List[TradeRecord]):
        """Entry and exit prices should be positive."""
        for i, trade in enumerate(trade_history):
            assert trade.entry_price > 0, (
                f"Trade {i+1}: entry_price {trade.entry_price} not positive"
            )
            assert trade.exit_price > 0, (
                f"Trade {i+1}: exit_price {trade.exit_price} not positive"
            )

    def test_valid_tick_value(self, trade_history: List[TradeRecord]):
        """Tick value should be positive."""
        for i, trade in enumerate(trade_history):
            assert trade.entry_tick_value > 0, (
                f"Trade {i+1}: tick_value {trade.entry_tick_value} not positive"
            )


class TestTradeRecordCompleteness:
    """Tests for trade record data completeness."""

    def test_all_required_fields_present(self, trade_history: List[TradeRecord]):
        """All trade records should have required fields populated."""
        for i, trade in enumerate(trade_history):
            assert trade.position_id, f"Trade {i+1}: missing position_id"
            assert trade.symbol, f"Trade {i+1}: missing symbol"
            assert trade.direction in ("LONG", "SHORT"), (
                f"Trade {i+1}: invalid direction {trade.direction}"
            )
            assert trade.digits > 0, f"Trade {i+1}: invalid digits {trade.digits}"
            assert trade.contract_size > 0, (
                f"Trade {i+1}: invalid contract_size {trade.contract_size}"
            )

    def test_timestamps_present(self, trade_history: List[TradeRecord]):
        """Entry and exit timestamps should be present."""
        for i, trade in enumerate(trade_history):
            assert trade.entry_time is not None, f"Trade {i+1}: missing entry_time"
            assert trade.exit_time is not None, f"Trade {i+1}: missing exit_time"

    def test_account_currency_present(self, trade_history: List[TradeRecord]):
        """Account currency should be present for audit trail."""
        for i, trade in enumerate(trade_history):
            assert trade.account_currency, (
                f"Trade {i+1}: missing account_currency"
            )
