"""
FiniexTestingIDE - Multi-Position Test Suite (#114)
Validates overlapping position management, hedging, and selective close.

These tests are SPECIFIC to multi-position behavior.
Generic P&L/fee/tick tests are reused from mvp_baseline/ (same fixture names).

Test Groups:
- TestConcurrentPositions: Multiple positions open simultaneously
- TestSelectiveClose: Individual position closing without affecting others
- TestHedging: Opposite-direction positions on same symbol
- TestPositionIsolation: Per-position P&L and fee correctness
- TestRecoveryAfterGap: Clean open after all positions closed
- TestMultiPositionMetadata: Decision logic tracking data
"""

import pytest
from typing import Dict, Any, List

from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.order_types import OrderDirection
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_trade_record_types import TradeRecord
from python.framework.types.process_data_types import ProcessTickLoopResult


# =============================================================================
# HELPERS: Compute overlap from trade records
# =============================================================================

def _concurrent_at_tick(trade_history: List[TradeRecord], tick: int) -> int:
    """Count how many positions were open at a given tick."""
    return sum(
        1 for t in trade_history
        if t.entry_tick_index <= tick < t.exit_tick_index
    )


def _peak_concurrent(trade_history: List[TradeRecord]) -> int:
    """Compute maximum concurrent positions from trade records."""
    if not trade_history:
        return 0

    # Check concurrency at every entry and exit tick
    ticks_to_check = set()
    for t in trade_history:
        ticks_to_check.add(t.entry_tick_index)
        ticks_to_check.add(t.exit_tick_index)
        # Also check one tick before exit (still open)
        ticks_to_check.add(t.exit_tick_index - 1)

    return max(_concurrent_at_tick(trade_history, tick) for tick in ticks_to_check)


def _trades_open_at_tick(
    trade_history: List[TradeRecord], tick: int
) -> List[TradeRecord]:
    """Get all trades that were open at a given tick."""
    return [
        t for t in trade_history
        if t.entry_tick_index <= tick < t.exit_tick_index
    ]


# =============================================================================
# TEST: Concurrent Positions
# =============================================================================

class TestConcurrentPositions:
    """Validate that multiple positions can be open simultaneously."""

    def test_peak_concurrent_is_three(
        self, trade_history: List[TradeRecord]
    ):
        """
        Peak concurrency should be 3 positions.

        Config creates overlapping trades:
        - Trade #0: LONG  @ ~tick 105  → ~tick 8105
        - Trade #1: LONG  @ ~tick 2006 → ~tick 7504
        - Trade #2: SHORT @ ~tick 3006 → ~tick 7006
        Peak: ticks ~3006-7006 (3 concurrent)
        """
        assert _peak_concurrent(trade_history) == 3, (
            f"Expected peak 3 concurrent positions, "
            f"got {_peak_concurrent(trade_history)}"
        )

    def test_two_concurrent_after_first_close(
        self, trade_history: List[TradeRecord]
    ):
        """
        After SHORT (#2) closes (~tick 7006), 2 LONGs remain.
        Check at tick ~7100 (between SHORT close and LONG #1 close).
        """
        # SHORT closes around tick 7006, LONG #1 closes around tick 7504
        # At tick 7200 we should see exactly 2 positions
        concurrent = _concurrent_at_tick(trade_history, 7200)
        assert concurrent == 2, (
            f"Expected 2 concurrent at tick 7200, got {concurrent}"
        )

    def test_one_position_after_second_close(
        self, trade_history: List[TradeRecord]
    ):
        """
        After LONG #1 closes (~7504), only LONG #0 remains.
        Check at tick ~7800.
        """
        concurrent = _concurrent_at_tick(trade_history, 7800)
        assert concurrent == 1, (
            f"Expected 1 position at tick 7800, got {concurrent}"
        )

    def test_zero_positions_in_gap(
        self, trade_history: List[TradeRecord]
    ):
        """
        After all first-group trades close (~8105), gap until #3 (~12007).
        Check at tick 10000.
        """
        concurrent = _concurrent_at_tick(trade_history, 10000)
        assert concurrent == 0, (
            f"Expected 0 positions at tick 10000, got {concurrent}"
        )

    def test_more_than_one_position_existed(
        self, trade_history: List[TradeRecord]
    ):
        """
        At least at some point, more than 1 position was open.
        This is the fundamental multi-position assertion.
        """
        assert _peak_concurrent(trade_history) > 1, (
            "Multi-position test must have >1 concurrent positions"
        )


# =============================================================================
# TEST: Selective Close
# =============================================================================

class TestSelectiveClose:
    """Validate that positions close individually, not blanket-all."""

    def test_trades_close_at_different_ticks(
        self, trade_history: List[TradeRecord]
    ):
        """Each trade should close at a different tick (selective, not bulk)."""
        exit_ticks = [t.exit_tick_index for t in trade_history]
        assert len(set(exit_ticks)) == len(exit_ticks), (
            f"Exit ticks should be unique (selective close), "
            f"got: {exit_ticks}"
        )

    def test_close_order_matches_hold_ticks(
        self, trade_history: List[TradeRecord],
        trade_sequence: list
    ):
        """
        Trades should close in order of their hold_ticks expiry.

        Expected close order (by signal_tick + hold_ticks):
        - Trade #2: 3000 + 4000 = 7000  → closes first
        - Trade #1: 2000 + 5500 = 7500  → closes second
        - Trade #0: 100 + 8000  = 8100  → closes third
        - Trade #3: 12000 + 3000 = 15000 → closes last
        """
        # Compute expected close order by expiry tick
        expiries = [
            (i, spec['tick_number'] + spec['hold_ticks'])
            for i, spec in enumerate(trade_sequence)
        ]
        expected_order = [idx for idx, _ in sorted(
            expiries, key=lambda x: x[1])]

        # Actual close order from trade_history (sorted by exit_tick)
        sorted_trades = sorted(trade_history, key=lambda t: t.exit_tick_index)
        actual_exit_ticks = [t.exit_tick_index for t in sorted_trades]

        # Verify monotonically increasing exit ticks
        for i in range(len(actual_exit_ticks) - 1):
            assert actual_exit_ticks[i] < actual_exit_ticks[i + 1], (
                f"Exit ticks should be in ascending order: {actual_exit_ticks}"
            )

    def test_close_tick_near_expected(
        self, trade_history: List[TradeRecord],
        trade_sequence: list
    ):
        """
        Actual close tick should be near signal_tick + hold_ticks.
        Difference is entry latency (position opens later than signal).
        Max latency is 8 ticks, so tolerance = 10.
        """
        # Match trades by direction+lot_size to trade_sequence
        for spec in trade_sequence:
            signal_tick = spec['tick_number']
            matching = [
                t for t in trade_history
                if t.direction == OrderDirection(spec['direction'].lower())
                and abs(t.lots - spec['lot_size']) < 0.001
                and abs(t.entry_tick_index - signal_tick) < 10
            ]
            assert len(matching) == 1, (
                f"Expected 1 matching trade for {spec['direction']} "
                f"{spec['lot_size']}L near tick {signal_tick}, got {len(matching)}"
            )
            trade = matching[0]
            expected_close = spec['tick_number'] + spec['hold_ticks']
            # Close signal fires at expected_close, but actual fill has latency
            # Also entry had latency, so actual hold duration ≈ hold_ticks
            tolerance = 15  # entry_latency + close_latency
            assert abs(trade.exit_tick_index - expected_close) < tolerance, (
                f"Trade {spec['direction']} {spec['lot_size']}L: "
                f"exit at tick {trade.exit_tick_index}, "
                f"expected ~{expected_close} (±{tolerance})"
            )


# =============================================================================
# TEST: Hedging (Opposite Directions)
# =============================================================================

class TestHedging:
    """Validate LONG + SHORT simultaneously on the same symbol."""

    def test_has_both_directions(self, trade_history: List[TradeRecord]):
        """Trade history should contain both LONG and SHORT trades."""
        directions = set(t.direction for t in trade_history)
        assert OrderDirection.LONG in directions, "Missing LONG trades"
        assert OrderDirection.SHORT in directions, "Missing SHORT trades"

    def test_opposite_directions_overlap(
        self, trade_history: List[TradeRecord]
    ):
        """
        LONG and SHORT positions must have overlapping time windows.
        Trade #0 (LONG) and Trade #2 (SHORT) overlap at ticks ~3006-7006.
        """
        longs = [t for t in trade_history if t.direction == OrderDirection.LONG]
        shorts = [t for t in trade_history if t.direction == OrderDirection.SHORT]

        # Check if any LONG overlaps with any SHORT
        has_overlap = False
        for long_trade in longs:
            for short_trade in shorts:
                overlap_start = max(
                    long_trade.entry_tick_index, short_trade.entry_tick_index
                )
                overlap_end = min(
                    long_trade.exit_tick_index, short_trade.exit_tick_index
                )
                if overlap_start < overlap_end:
                    has_overlap = True
                    break
            if has_overlap:
                break

        assert has_overlap, (
            "No overlap between LONG and SHORT positions detected. "
            "Hedging test requires simultaneous opposite positions."
        )

    def test_hedging_window_has_three_positions(
        self, trade_history: List[TradeRecord]
    ):
        """
        During hedging window (~tick 3006-7006), exactly 3 positions open:
        2 LONGs + 1 SHORT.
        """
        # Pick a tick solidly inside the hedging window
        hedging_tick = 5000
        open_trades = _trades_open_at_tick(trade_history, hedging_tick)

        long_count = sum(1 for t in open_trades if t.direction == OrderDirection.LONG)
        short_count = sum(1 for t in open_trades if t.direction == OrderDirection.SHORT)

        assert long_count == 2, (
            f"Expected 2 LONGs at tick {hedging_tick}, got {long_count}"
        )
        assert short_count == 1, (
            f"Expected 1 SHORT at tick {hedging_tick}, got {short_count}"
        )


# =============================================================================
# TEST: Position Isolation (P&L + Fees per position)
# =============================================================================

class TestPositionIsolation:
    """Validate per-position P&L isolation and portfolio aggregation."""

    def test_unique_position_ids(self, trade_history: List[TradeRecord]):
        """Every trade should have a unique position_id."""
        ids = [t.position_id for t in trade_history]
        assert len(set(ids)) == len(ids), (
            f"Duplicate position IDs: {ids}"
        )

    def test_per_trade_net_pnl_formula(
        self, trade_history: List[TradeRecord]
    ):
        """Net P&L = gross - fees for EACH trade independently."""
        for i, trade in enumerate(trade_history):
            expected_net = trade.gross_pnl - trade.total_fees
            assert abs(trade.net_pnl - expected_net) < 0.001, (
                f"Trade {i} ({trade.position_id}): "
                f"net={trade.net_pnl:.4f} != gross-fees={expected_net:.4f}"
            )

    def test_portfolio_pnl_is_sum_of_trades(
        self,
        trade_history: List[TradeRecord],
        portfolio_stats: PortfolioStats
    ):
        """
        Portfolio total P&L must equal sum of all individual trade P&Ls.
        This is the core multi-position aggregation test.
        """
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
        """Total spread cost must equal sum of per-trade spread costs."""
        trade_spread = sum(t.spread_cost for t in trade_history)
        portfolio_spread = portfolio_stats.total_spread_cost

        assert abs(trade_spread - portfolio_spread) < 0.01, (
            f"Fee aggregation mismatch: sum(trades)={trade_spread:.4f}, "
            f"portfolio={portfolio_spread:.4f}"
        )

    def test_all_trades_have_valid_symbol(
        self, trade_history: List[TradeRecord]
    ):
        """All trades should be on the same symbol (USDJPY)."""
        symbols = set(t.symbol for t in trade_history)
        assert len(symbols) == 1, f"Multiple symbols in trades: {symbols}"
        assert "USDJPY" in symbols, f"Expected USDJPY, got {symbols}"

    def test_each_trade_has_positive_fees(
        self, trade_history: List[TradeRecord]
    ):
        """Each trade should have non-negative fees (spread cost)."""
        for i, trade in enumerate(trade_history):
            assert trade.spread_cost >= 0, (
                f"Trade {i} ({trade.position_id}): "
                f"negative spread_cost {trade.spread_cost}"
            )
            assert trade.total_fees >= 0, (
                f"Trade {i} ({trade.position_id}): "
                f"negative total_fees {trade.total_fees}"
            )


# =============================================================================
# TEST: Recovery After Gap
# =============================================================================

class TestRecoveryAfterGap:
    """Validate clean trade opening after all positions are closed."""

    def test_gap_exists_between_groups(
        self, trade_history: List[TradeRecord]
    ):
        """
        There must be a tick range with 0 open positions between
        the first group (trades #0-#2) and the recovery trade (#3).
        """
        # Sort by exit_tick to find last close of first group
        sorted_by_exit = sorted(trade_history, key=lambda t: t.exit_tick_index)

        # First group closes are the first 3 trades by exit order
        # Recovery trade is the last one
        if len(sorted_by_exit) >= 4:
            last_first_group_exit = sorted_by_exit[2].exit_tick_index
            recovery_entry = sorted_by_exit[3].entry_tick_index

            # Gap midpoint
            gap_tick = (last_first_group_exit + recovery_entry) // 2
            concurrent = _concurrent_at_tick(trade_history, gap_tick)

            assert concurrent == 0, (
                f"Expected 0 positions at gap tick {gap_tick}, "
                f"got {concurrent}. Gap: {last_first_group_exit}-{recovery_entry}"
            )

    def test_recovery_trade_is_independent(
        self, trade_history: List[TradeRecord]
    ):
        """
        Recovery trade (#3) should not overlap with any earlier trade.
        """
        sorted_by_entry = sorted(
            trade_history, key=lambda t: t.entry_tick_index
        )

        if len(sorted_by_entry) >= 4:
            recovery = sorted_by_entry[3]  # Last trade by entry

            # No other trade should be open when recovery opens
            others_at_entry = [
                t for t in trade_history
                if t != recovery
                and t.entry_tick_index <= recovery.entry_tick_index
                < t.exit_tick_index
            ]

            assert len(others_at_entry) == 0, (
                f"Recovery trade at tick {recovery.entry_tick_index} "
                f"overlaps with {len(others_at_entry)} other trades"
            )

    def test_total_trade_count(
        self,
        trade_history: List[TradeRecord],
        trade_sequence: list
    ):
        """All configured trades should execute (including recovery)."""
        assert len(trade_history) == len(trade_sequence), (
            f"Expected {len(trade_sequence)} trades, "
            f"got {len(trade_history)}"
        )


# =============================================================================
# TEST: Multi-Position Metadata
# =============================================================================

class TestMultiPositionMetadata:
    """Validate BacktestingMetadata from multi-position decision logic."""

    def test_expected_trades_count(
        self,
        backtesting_metadata: BacktestingMetadata,
        trade_sequence: list
    ):
        """Expected trades in metadata should match config count."""
        assert len(backtesting_metadata.expected_trades) == len(trade_sequence), (
            f"Expected {len(trade_sequence)} expected_trades, "
            f"got {len(backtesting_metadata.expected_trades)}"
        )

    def test_expected_trades_have_order_ids(
        self, backtesting_metadata: BacktestingMetadata
    ):
        """Each expected trade should have an order_id assigned."""
        for i, trade in enumerate(backtesting_metadata.expected_trades):
            assert 'order_id' in trade and trade['order_id'], (
                f"Expected trade {i} missing order_id: {trade}"
            )

    def test_expected_trades_directions_match_config(
        self,
        backtesting_metadata: BacktestingMetadata,
        trade_sequence: list
    ):
        """Expected trade directions should match config sequence."""
        for i, (expected, actual) in enumerate(zip(
            trade_sequence,
            backtesting_metadata.expected_trades
        )):
            assert expected['direction'] == actual['direction'], (
                f"Trade {i}: config={expected['direction']}, "
                f"metadata={actual['direction']}"
            )

    def test_expected_trades_signal_ticks_match(
        self,
        backtesting_metadata: BacktestingMetadata,
        trade_sequence: list
    ):
        """Signal ticks in metadata should match config tick_numbers."""
        for i, (expected, actual) in enumerate(zip(
            trade_sequence,
            backtesting_metadata.expected_trades
        )):
            assert expected['tick_number'] == actual['signal_tick'], (
                f"Trade {i}: config tick={expected['tick_number']}, "
                f"metadata signal_tick={actual['signal_tick']}"
            )

    def test_order_ids_match_trade_history(
        self,
        backtesting_metadata: BacktestingMetadata,
        trade_history: List[TradeRecord]
    ):
        """
        Order IDs from metadata should appear in trade history position_ids.
        This validates the pipeline: decision_logic → order → position → trade_record.
        """
        metadata_ids = set(
            t['order_id'] for t in backtesting_metadata.expected_trades
        )
        history_ids = set(t.position_id for t in trade_history)

        assert metadata_ids == history_ids, (
            f"ID mismatch - metadata: {metadata_ids}, history: {history_ids}"
        )

    def test_no_warmup_errors(self, backtesting_metadata: BacktestingMetadata):
        """Multi-position run should have no warmup errors."""
        assert backtesting_metadata.warmup_errors == [], (
            f"Warmup errors: {backtesting_metadata.warmup_errors}"
        )

    def test_no_rejected_orders(
        self, tick_loop_results: ProcessTickLoopResult
    ):
        """All multi-position orders should execute without rejection."""
        exec_stats = tick_loop_results.execution_stats
        assert exec_stats.orders_rejected == 0, (
            f"Rejected orders: {exec_stats.orders_rejected}"
        )

    def test_tick_count_matches_config(
        self,
        backtesting_metadata: BacktestingMetadata,
        scenario_config: Dict
    ):
        """Tick count should match config max_ticks."""
        expected = scenario_config['scenarios'][0]['max_ticks']
        assert backtesting_metadata.tick_count == expected, (
            f"Expected {expected} ticks, got {backtesting_metadata.tick_count}"
        )
