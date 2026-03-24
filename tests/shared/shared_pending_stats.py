"""
FiniexTestingIDE - Shared Pending Order Statistics Tests
Reusable test classes for pending stats validation across test suites.

Validates:
- Synthetic close path (no false force-closed from end-of-scenario cleanup)
- Real force-closed detection (stuck-in-pipeline orders)
- Latency statistics (avg/min/max populated)
- Outcome counting (filled matches expected)
- Anomaly records with reason field

Used by: pending_stats test suite
Import these classes into suite-specific test_pending_stats.py files.
"""

import pytest
from typing import List

from python.framework.types.trading_env_types.pending_order_stats_types import PendingOrderStats
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrderOutcome


class TestPendingStatsBaseline:
    """Tests for pending order statistics — baseline assertions."""

    def test_pending_stats_exists(self, pending_stats: PendingOrderStats):
        """Pending stats should be populated after scenario execution."""
        assert pending_stats is not None
        assert pending_stats.total_resolved > 0, "No pending orders were resolved"

    def test_total_resolved_consistency(self, pending_stats: PendingOrderStats):
        """Total resolved should equal sum of all outcome counts."""
        expected = (
            pending_stats.total_filled
            + pending_stats.total_rejected
            + pending_stats.total_timed_out
            + pending_stats.total_force_closed
        )
        assert pending_stats.total_resolved == expected, (
            f"total_resolved={pending_stats.total_resolved} != "
            f"filled({pending_stats.total_filled}) + rejected({pending_stats.total_rejected}) + "
            f"timed_out({pending_stats.total_timed_out}) + force_closed({pending_stats.total_force_closed})"
        )

    def test_no_rejected_orders(self, pending_stats: PendingOrderStats):
        """No orders should be rejected in normal backtesting."""
        assert pending_stats.total_rejected == 0, (
            f"Unexpected rejections: {pending_stats.total_rejected}"
        )

    def test_no_timed_out_orders(self, pending_stats: PendingOrderStats):
        """No orders should time out in simulation mode."""
        assert pending_stats.total_timed_out == 0, (
            f"Unexpected timeouts: {pending_stats.total_timed_out}"
        )

    def test_latency_stats_populated(self, pending_stats: PendingOrderStats):
        """Millisecond-based latency stats should be populated."""
        assert pending_stats.avg_latency_ms > 0, "avg_latency_ms not set"
        assert pending_stats.min_latency_ms is not None, "min_latency_ms not set"
        assert pending_stats.max_latency_ms is not None, "max_latency_ms not set"
        assert pending_stats.min_latency_ms <= pending_stats.max_latency_ms, (
            f"min ({pending_stats.min_latency_ms}) > max ({pending_stats.max_latency_ms})"
        )

    def test_latency_avg_in_range(self, pending_stats: PendingOrderStats):
        """Average latency should be between min and max."""
        assert pending_stats.min_latency_ms <= pending_stats.avg_latency_ms, (
            f"avg ({pending_stats.avg_latency_ms}) < min ({pending_stats.min_latency_ms})"
        )
        assert pending_stats.avg_latency_ms <= pending_stats.max_latency_ms, (
            f"avg ({pending_stats.avg_latency_ms}) > max ({pending_stats.max_latency_ms})"
        )


class TestSyntheticCloseNotCounted:
    """Tests that end-of-scenario position closes don't produce false force-closed."""

    def test_filled_count_matches_trade_lifecycle(
        self,
        pending_stats: PendingOrderStats,
        portfolio_stats: PortfolioStats
    ):
        """
        Filled count should reflect actual order fills through the latency pipeline.

        Each completed trade = 1 open fill + 1 close fill = 2 filled.
        End-of-scenario synthetic closes bypass the pipeline and are NOT counted.
        """
        completed_trades = portfolio_stats.total_trades
        # Each completed trade has open + close through pipeline
        # But the last trade may be force-closed (close didn't fill via pipeline)
        # So filled >= completed_trades (at least the opens filled)
        assert pending_stats.total_filled >= completed_trades, (
            f"total_filled ({pending_stats.total_filled}) < "
            f"total_trades ({completed_trades})"
        )


class TestForceClosedDetection:
    """Tests that genuine stuck-in-pipeline orders are correctly detected."""

    def test_force_closed_count(self, pending_stats: PendingOrderStats):
        """
        Should have at least 1 force-closed order.

        Trade 2 opens at tick 5000 (last tick). No subsequent tick exists
        to fill the order, so it remains pending and is force-closed at
        scenario end.
        """
        assert pending_stats.total_force_closed >= 1, (
            f"Expected at least 1 force-closed, got {pending_stats.total_force_closed}"
        )

    def test_anomaly_records_populated(self, pending_stats: PendingOrderStats):
        """Anomaly records should exist for force-closed orders."""
        assert len(pending_stats.anomaly_orders) >= 1, (
            f"Expected anomaly records, got {len(pending_stats.anomaly_orders)}"
        )

    def test_anomaly_record_has_reason(self, pending_stats: PendingOrderStats):
        """Each anomaly record should have a reason field."""
        for record in pending_stats.anomaly_orders:
            assert record.reason is not None, (
                f"Anomaly record {record.order_id} has no reason"
            )

    def test_anomaly_reason_is_scenario_end(self, pending_stats: PendingOrderStats):
        """Force-closed records from scenario end should have reason='scenario_end'."""
        for record in pending_stats.anomaly_orders:
            if record.outcome == PendingOrderOutcome.FORCE_CLOSED:
                assert record.reason == "scenario_end", (
                    f"Expected reason='scenario_end', got '{record.reason}' "
                    f"for order {record.order_id}"
                )

    def test_anomaly_record_has_latency(self, pending_stats: PendingOrderStats):
        """Force-closed records should have latency information (>= 0ms)."""
        for record in pending_stats.anomaly_orders:
            if record.outcome == PendingOrderOutcome.FORCE_CLOSED:
                assert record.latency_ms is not None, (
                    f"Force-closed record {record.order_id} has no latency_ms"
                )
                assert record.latency_ms >= 0, (
                    f"Force-closed record {record.order_id} has negative latency_ms"
                )
