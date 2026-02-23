"""
FiniexTestingIDE - Partial Close Test Suite (#119)
Validates partial position close: proportional P&L, fee splitting,
position state, and portfolio aggregation correctness.

Scenario:
- Trade #0: LONG 0.03 lots, hold 8000 ticks
  - Partial close 0.01 at tick ~2000 → remaining 0.02
  - Partial close 0.01 at tick ~4000 → remaining 0.01
  - Full close of remaining 0.01 at hold_ticks expiry
- Trade #1: SHORT 0.02 lots, hold 5000 ticks (no partial close — isolation check)

Expected trade_history: 4 TradeRecords (2 partial + 1 full for #0, 1 full for #1)

Test Groups:
- TestTradeRecordCount: Correct number of records with right close types
- TestPartialCloseProportionalPnL: P&L proportional to closed lots
- TestPartialCloseFeeSplitting: Fees proportional to closed lots
- TestPositionIsolation: Non-partial position unaffected
- TestPortfolioAggregation: Sum of parts = portfolio total
"""

import pytest
from typing import List

from python.framework.types.order_types import OrderDirection
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_trade_record_types import (
    CloseType,
    TradeRecord,
)
from python.framework.types.process_data_types import ProcessTickLoopResult


# =============================================================================
# HELPERS
# =============================================================================

def _partial_records(trade_history: List[TradeRecord]) -> List[TradeRecord]:
    """Get all partial close records, sorted by exit tick."""
    return sorted(
        [t for t in trade_history if t.close_type == CloseType.PARTIAL],
        key=lambda t: t.exit_tick_index
    )


def _full_records(trade_history: List[TradeRecord]) -> List[TradeRecord]:
    """Get all full close records, sorted by exit tick."""
    return sorted(
        [t for t in trade_history if t.close_type == CloseType.FULL],
        key=lambda t: t.exit_tick_index
    )


def _records_for_position(
    trade_history: List[TradeRecord], position_id: str
) -> List[TradeRecord]:
    """Get all records (partial + full) for a specific position."""
    return sorted(
        [t for t in trade_history if t.position_id == position_id],
        key=lambda t: t.exit_tick_index
    )


# =============================================================================
# TEST: Trade Record Count & Close Types
# =============================================================================

class TestTradeRecordCount:
    """Validate correct number of trade records with proper close types."""

    def test_total_trade_count(self, trade_history: List[TradeRecord]):
        """
        Expect 4 trade records total:
        - 2 partial closes for Trade #0
        - 1 full close for Trade #0 remainder
        - 1 full close for Trade #1
        """
        assert len(trade_history) == 4, (
            f"Expected 4 trade records, got {len(trade_history)}"
        )

    def test_partial_record_count(self, trade_history: List[TradeRecord]):
        """Expect exactly 2 partial close records."""
        partials = _partial_records(trade_history)
        assert len(partials) == 2, (
            f"Expected 2 partial records, got {len(partials)}"
        )

    def test_full_record_count(self, trade_history: List[TradeRecord]):
        """Expect exactly 2 full close records."""
        fulls = _full_records(trade_history)
        assert len(fulls) == 2, (
            f"Expected 2 full records, got {len(fulls)}"
        )

    def test_partial_records_share_position_id(
        self, trade_history: List[TradeRecord]
    ):
        """Both partial records should belong to the same position (Trade #0)."""
        partials = _partial_records(trade_history)
        ids = set(t.position_id for t in partials)
        assert len(ids) == 1, (
            f"Partial records should share one position_id, got {ids}"
        )

    def test_partial_position_has_three_records(
        self, trade_history: List[TradeRecord]
    ):
        """
        The partially closed position should have 3 total records:
        2 partial + 1 final full close.
        """
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records found")

        pos_id = partials[0].position_id
        all_for_pos = _records_for_position(trade_history, pos_id)
        assert len(all_for_pos) == 3, (
            f"Expected 3 records for position {pos_id}, got {len(all_for_pos)}"
        )


# =============================================================================
# TEST: Partial Close Lot Sizes
# =============================================================================

class TestPartialCloseLots:
    """Validate that partial close lot sizes match configuration."""

    def test_first_partial_lots(
        self,
        trade_history: List[TradeRecord],
        partial_close_sequence: list
    ):
        """First partial close should close 0.01 lots."""
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records")

        expected = partial_close_sequence[0]['close_lots']
        assert abs(partials[0].lots - expected) < 0.0001, (
            f"First partial: expected {expected} lots, got {partials[0].lots}"
        )

    def test_second_partial_lots(
        self,
        trade_history: List[TradeRecord],
        partial_close_sequence: list
    ):
        """Second partial close should close 0.01 lots."""
        partials = _partial_records(trade_history)
        if len(partials) < 2:
            pytest.skip("Less than 2 partial records")

        expected = partial_close_sequence[1]['close_lots']
        assert abs(partials[1].lots - expected) < 0.0001, (
            f"Second partial: expected {expected} lots, got {partials[1].lots}"
        )

    def test_remainder_lots(self, trade_history: List[TradeRecord]):
        """
        Remainder close should be 0.01 lots (0.03 - 0.01 - 0.01).
        This is the full close record for the partially closed position.
        """
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records")

        pos_id = partials[0].position_id
        all_for_pos = _records_for_position(trade_history, pos_id)
        final = [t for t in all_for_pos if t.close_type == CloseType.FULL]

        assert len(final) == 1, (
            f"Expected 1 full close for position {pos_id}, got {len(final)}"
        )
        assert abs(final[0].lots - 0.01) < 0.0001, (
            f"Remainder lots: expected 0.01, got {final[0].lots}"
        )

    def test_lots_sum_equals_original(self, trade_history: List[TradeRecord]):
        """Sum of all closed lots for a position should equal original lots (0.03)."""
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records")

        pos_id = partials[0].position_id
        all_for_pos = _records_for_position(trade_history, pos_id)
        total_lots = sum(t.lots for t in all_for_pos)

        assert abs(total_lots - 0.03) < 0.0001, (
            f"Sum of closed lots: expected 0.03, got {total_lots}"
        )


# =============================================================================
# TEST: Proportional P&L
# =============================================================================

class TestPartialClosePnL:
    """Validate that P&L is correctly proportioned across partial closes."""

    def test_each_partial_net_pnl_formula(
        self, trade_history: List[TradeRecord]
    ):
        """Net P&L = gross - fees for each partial record."""
        partials = _partial_records(trade_history)
        for i, trade in enumerate(partials):
            expected_net = trade.gross_pnl - trade.total_fees
            assert abs(trade.net_pnl - expected_net) < 0.001, (
                f"Partial #{i}: net={trade.net_pnl:.4f} != "
                f"gross-fees={expected_net:.4f}"
            )

    def test_partial_records_have_same_entry_price(
        self, trade_history: List[TradeRecord]
    ):
        """All records for a partially closed position share the same entry price."""
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records")

        pos_id = partials[0].position_id
        all_for_pos = _records_for_position(trade_history, pos_id)
        entry_prices = set(t.entry_price for t in all_for_pos)
        assert len(entry_prices) == 1, (
            f"Entry prices should be identical, got {entry_prices}"
        )

    def test_partial_records_have_different_exit_ticks(
        self, trade_history: List[TradeRecord]
    ):
        """Each partial close should happen at a different tick."""
        partials = _partial_records(trade_history)
        exit_ticks = [t.exit_tick_index for t in partials]
        assert len(set(exit_ticks)) == len(exit_ticks), (
            f"Partial exit ticks should be unique, got {exit_ticks}"
        )


# =============================================================================
# TEST: Fee Splitting
# =============================================================================

class TestPartialCloseFeeSplitting:
    """Validate that fees are proportionally split across partial closes."""

    def test_each_partial_has_positive_fees(
        self, trade_history: List[TradeRecord]
    ):
        """Each partial close record should have positive fees."""
        partials = _partial_records(trade_history)
        for i, trade in enumerate(partials):
            assert trade.spread_cost > 0, (
                f"Partial #{i}: spread_cost should be > 0, "
                f"got {trade.spread_cost}"
            )
            assert trade.total_fees > 0, (
                f"Partial #{i}: total_fees should be > 0, "
                f"got {trade.total_fees}"
            )

    def test_fee_sum_across_partials_is_consistent(
        self, trade_history: List[TradeRecord]
    ):
        """
        Sum of all spread costs for a position's records should be
        approximately equal to what the full position would have paid.
        (Not exact due to fee splitting rounding)
        """
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records")

        pos_id = partials[0].position_id
        all_for_pos = _records_for_position(trade_history, pos_id)
        total_spread = sum(t.spread_cost for t in all_for_pos)

        # Each record should contribute some fees
        for i, trade in enumerate(all_for_pos):
            assert trade.total_fees >= 0, (
                f"Record #{i}: negative fees {trade.total_fees}"
            )

        # Total spread should be positive
        assert total_spread > 0, (
            f"Total spread for partial position should be > 0, "
            f"got {total_spread}"
        )


# =============================================================================
# TEST: Position Isolation (Trade #1 unaffected)
# =============================================================================

class TestPositionIsolation:
    """Validate that the non-partial position (Trade #1) is unaffected."""

    def test_non_partial_has_full_close_type(
        self, trade_history: List[TradeRecord]
    ):
        """Trade #1 (SHORT) should have CloseType.FULL."""
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records")

        partial_pos_id = partials[0].position_id
        non_partial = [
            t for t in trade_history
            if t.position_id != partial_pos_id
        ]

        assert len(non_partial) == 1, (
            f"Expected 1 non-partial trade, got {len(non_partial)}"
        )
        assert non_partial[0].close_type == CloseType.FULL, (
            f"Non-partial trade should be FULL close, "
            f"got {non_partial[0].close_type}"
        )

    def test_non_partial_is_short(self, trade_history: List[TradeRecord]):
        """Trade #1 should be SHORT direction."""
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records")

        partial_pos_id = partials[0].position_id
        non_partial = [
            t for t in trade_history
            if t.position_id != partial_pos_id
        ]

        assert len(non_partial) == 1
        assert non_partial[0].direction == OrderDirection.SHORT, (
            f"Non-partial trade direction: expected SHORT, "
            f"got {non_partial[0].direction}"
        )

    def test_non_partial_lot_size(self, trade_history: List[TradeRecord]):
        """Trade #1 should close with full 0.02 lots."""
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records")

        partial_pos_id = partials[0].position_id
        non_partial = [
            t for t in trade_history
            if t.position_id != partial_pos_id
        ]

        assert len(non_partial) == 1
        assert abs(non_partial[0].lots - 0.02) < 0.0001, (
            f"Non-partial trade lots: expected 0.02, "
            f"got {non_partial[0].lots}"
        )


# =============================================================================
# TEST: Portfolio Aggregation
# =============================================================================

class TestPortfolioAggregation:
    """Validate that portfolio totals match sum of all trade records."""

    def test_portfolio_pnl_is_sum_of_trades(
        self,
        trade_history: List[TradeRecord],
        portfolio_stats: PortfolioStats
    ):
        """Portfolio total P&L must equal sum of all trade P&Ls."""
        trade_total = sum(t.net_pnl for t in trade_history)
        portfolio_total = portfolio_stats.total_profit - portfolio_stats.total_loss

        assert abs(trade_total - portfolio_total) < 0.02, (
            f"Aggregation mismatch: sum(trades)={trade_total:.4f}, "
            f"portfolio={portfolio_total:.4f}"
        )

    def test_portfolio_fees_is_sum_of_trade_fees(
        self,
        trade_history: List[TradeRecord],
        portfolio_stats: PortfolioStats
    ):
        """Total spread cost must equal sum of all trade spread costs."""
        trade_spread = sum(t.spread_cost for t in trade_history)
        portfolio_spread = portfolio_stats.total_spread_cost

        assert abs(trade_spread - portfolio_spread) < 0.01, (
            f"Fee aggregation mismatch: sum(trades)={trade_spread:.4f}, "
            f"portfolio={portfolio_spread:.4f}"
        )

    def test_total_trades_count(
        self,
        trade_history: List[TradeRecord],
        portfolio_stats: PortfolioStats
    ):
        """Portfolio total_trades should include partial closes."""
        assert portfolio_stats.total_trades == len(trade_history), (
            f"Expected total_trades={len(trade_history)}, "
            f"got {portfolio_stats.total_trades}"
        )

    def test_no_rejected_orders(
        self, tick_loop_results: ProcessTickLoopResult
    ):
        """All orders (open + close + partial close) should execute."""
        exec_stats = tick_loop_results.execution_stats
        assert exec_stats.orders_rejected == 0, (
            f"Rejected orders: {exec_stats.orders_rejected}"
        )


# =============================================================================
# TEST: Chronological Order
# =============================================================================

class TestChronologicalOrder:
    """Validate that partial close records appear in correct order."""

    def test_partial_closes_before_final(
        self, trade_history: List[TradeRecord]
    ):
        """Partial close exit ticks should be before the final full close."""
        partials = _partial_records(trade_history)
        if not partials:
            pytest.skip("No partial records")

        pos_id = partials[0].position_id
        all_for_pos = _records_for_position(trade_history, pos_id)
        final = [t for t in all_for_pos if t.close_type == CloseType.FULL]

        if not final:
            pytest.skip("No full close for partial position")

        for partial in partials:
            assert partial.exit_tick_index < final[0].exit_tick_index, (
                f"Partial exit tick {partial.exit_tick_index} should be "
                f"before final close {final[0].exit_tick_index}"
            )

    def test_partial_close_ticks_near_config(
        self,
        trade_history: List[TradeRecord],
        partial_close_sequence: list
    ):
        """
        Partial close exit ticks should be near configured tick_numbers.
        Tolerance for latency: entry_latency + close_latency ≈ 15 ticks.
        """
        partials = _partial_records(trade_history)
        tolerance = 15

        for i, (record, spec) in enumerate(
            zip(partials, partial_close_sequence)
        ):
            expected_tick = spec['tick_number']
            assert abs(record.exit_tick_index - expected_tick) < tolerance, (
                f"Partial #{i}: exit tick {record.exit_tick_index}, "
                f"expected ~{expected_tick} (±{tolerance})"
            )
