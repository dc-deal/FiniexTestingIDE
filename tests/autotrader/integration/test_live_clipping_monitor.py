"""
LiveClippingMonitor — Unit Tests

Tests clipping detection, counter accuracy, periodic reports,
session summaries, queue depth tracking, and edge cases.

All tests use controlled inputs (no real ticks, no time dependency).
"""

import time
from unittest.mock import patch

from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor


# --- Helpers ---

def ms_to_ns(ms: float) -> int:
    """Convert milliseconds to nanoseconds for record_tick() input."""
    return int(ms * 1_000_000)


# ============================================
# Clipping Detection
# ============================================

class TestClippingDetection:
    """Core clipping logic: processing_ms > tick_delta_ms."""

    def test_no_clipping_when_processing_faster(self):
        """No clipping when processing time < tick delta."""
        monitor = LiveClippingMonitor()
        # Processing: 0.5ms, tick delta: 10ms → no clipping
        monitor.record_tick(ms_to_ns(0.5), tick_delta_ms=10.0)

        summary = monitor.get_session_summary()
        assert summary.ticks_clipped == 0
        assert summary.clipping_ratio == 0.0
        assert summary.max_stale_ms == 0.0

    def test_clipping_detected_when_processing_slower(self):
        """Clipping detected when processing time > tick delta."""
        monitor = LiveClippingMonitor()
        # Processing: 15ms, tick delta: 10ms → clipped, stale = 5ms
        monitor.record_tick(ms_to_ns(15.0), tick_delta_ms=10.0)

        summary = monitor.get_session_summary()
        assert summary.ticks_clipped == 1
        assert summary.clipping_ratio == 1.0
        assert summary.max_stale_ms == 5.0

    def test_no_clipping_on_equal_times(self):
        """No clipping when processing time == tick delta (boundary)."""
        monitor = LiveClippingMonitor()
        monitor.record_tick(ms_to_ns(10.0), tick_delta_ms=10.0)

        summary = monitor.get_session_summary()
        assert summary.ticks_clipped == 0

    def test_no_clipping_on_zero_tick_delta(self):
        """Zero tick delta (first tick) never triggers clipping."""
        monitor = LiveClippingMonitor()
        monitor.record_tick(ms_to_ns(5.0), tick_delta_ms=0.0)

        summary = monitor.get_session_summary()
        assert summary.ticks_clipped == 0
        assert summary.total_ticks == 1

    def test_no_clipping_on_negative_tick_delta(self):
        """Negative tick delta (clock anomaly) never triggers clipping."""
        monitor = LiveClippingMonitor()
        monitor.record_tick(ms_to_ns(5.0), tick_delta_ms=-1.0)

        summary = monitor.get_session_summary()
        assert summary.ticks_clipped == 0


# ============================================
# Counter Accuracy
# ============================================

class TestCounterAccuracy:
    """Verifies counters, averages, and max tracking over multiple ticks."""

    def test_mixed_clipping_sequence(self):
        """Mix of clipped and non-clipped ticks produces correct counts."""
        monitor = LiveClippingMonitor()
        # Tick 1: 0.5ms processing, 10ms delta → no clip
        monitor.record_tick(ms_to_ns(0.5), tick_delta_ms=10.0)
        # Tick 2: 12ms processing, 10ms delta → clipped (stale=2ms)
        monitor.record_tick(ms_to_ns(12.0), tick_delta_ms=10.0)
        # Tick 3: 8ms processing, 10ms delta → no clip
        monitor.record_tick(ms_to_ns(8.0), tick_delta_ms=10.0)
        # Tick 4: 25ms processing, 10ms delta → clipped (stale=15ms)
        monitor.record_tick(ms_to_ns(25.0), tick_delta_ms=10.0)

        summary = monitor.get_session_summary()
        assert summary.total_ticks == 4
        assert summary.ticks_clipped == 2
        assert summary.clipping_ratio == 0.5
        assert summary.max_stale_ms == 15.0
        # avg_stale = (2 + 15) / 2 = 8.5
        assert summary.avg_stale_ms == 8.5

    def test_max_processing_tracked(self):
        """Max processing time tracked across all ticks."""
        monitor = LiveClippingMonitor()
        monitor.record_tick(ms_to_ns(1.0), tick_delta_ms=100.0)
        monitor.record_tick(ms_to_ns(5.0), tick_delta_ms=100.0)
        monitor.record_tick(ms_to_ns(3.0), tick_delta_ms=100.0)

        summary = monitor.get_session_summary()
        assert summary.max_processing_ms == 5.0

    def test_avg_processing_across_all_ticks(self):
        """Average processing includes ALL ticks (not just clipped)."""
        monitor = LiveClippingMonitor()
        monitor.record_tick(ms_to_ns(2.0), tick_delta_ms=100.0)
        monitor.record_tick(ms_to_ns(4.0), tick_delta_ms=100.0)
        monitor.record_tick(ms_to_ns(6.0), tick_delta_ms=100.0)

        summary = monitor.get_session_summary()
        assert summary.avg_processing_ms == 4.0

    def test_processing_times_list_recorded(self):
        """All individual processing times stored in processing_times_ms."""
        monitor = LiveClippingMonitor()
        monitor.record_tick(ms_to_ns(1.5), tick_delta_ms=100.0)
        monitor.record_tick(ms_to_ns(2.5), tick_delta_ms=100.0)
        monitor.record_tick(ms_to_ns(3.5), tick_delta_ms=100.0)

        summary = monitor.get_session_summary()
        assert len(summary.processing_times_ms) == 3
        assert summary.processing_times_ms[0] == 1.5
        assert summary.processing_times_ms[1] == 2.5
        assert summary.processing_times_ms[2] == 3.5


# ============================================
# Queue Depth Tracking
# ============================================

class TestQueueDepthTracking:
    """Phase 5: Queue depth monitoring."""

    def test_max_queue_depth_tracked(self):
        """Max queue depth updates correctly."""
        monitor = LiveClippingMonitor()
        monitor.record_queue_depth(3)
        monitor.record_queue_depth(7)
        monitor.record_queue_depth(5)

        summary = monitor.get_session_summary()
        assert summary.max_queue_depth == 7

    def test_zero_queue_depth(self):
        """Zero depth is valid (empty queue)."""
        monitor = LiveClippingMonitor()
        monitor.record_queue_depth(0)

        summary = monitor.get_session_summary()
        assert summary.max_queue_depth == 0


# ============================================
# Periodic Reports
# ============================================

class TestPeriodicReports:
    """Phase 4: Interval-based periodic reporting."""

    def test_no_report_before_interval(self):
        """get_periodic_report() returns None before interval elapses."""
        monitor = LiveClippingMonitor(report_interval_s=60.0)
        monitor.record_tick(ms_to_ns(1.0), tick_delta_ms=10.0)

        report = monitor.get_periodic_report()
        assert report is None

    def test_report_after_interval(self):
        """get_periodic_report() returns report after interval elapses."""
        monitor = LiveClippingMonitor(report_interval_s=1.0)
        monitor.record_tick(ms_to_ns(1.0), tick_delta_ms=10.0)
        monitor.record_tick(ms_to_ns(15.0), tick_delta_ms=10.0)  # clipped
        monitor.record_queue_depth(4)

        # Fast-forward monotonic clock past interval
        with patch('python.framework.autotrader.live_clipping_monitor.time.monotonic',
                   return_value=monitor._last_report_time + 2.0):
            report = monitor.get_periodic_report()

        assert report is not None
        assert report.interval_ticks == 2
        assert report.interval_clipped == 1
        assert report.interval_max_stale_ms == 5.0
        assert report.interval_avg_stale_ms == 5.0
        assert report.interval_max_processing_ms == 15.0
        assert report.interval_avg_processing_ms == 8.0  # (1+15)/2
        assert report.interval_max_queue_depth == 4

    def test_interval_counters_reset_after_report(self):
        """Interval counters reset to zero after report generation."""
        monitor = LiveClippingMonitor(report_interval_s=0.0)
        monitor.record_tick(ms_to_ns(20.0), tick_delta_ms=10.0)

        # Force interval elapsed (interval_s=0 → always elapsed)
        report = monitor.get_periodic_report()
        assert report is not None
        assert report.interval_ticks == 1

        # Record another tick — new interval
        monitor.record_tick(ms_to_ns(2.0), tick_delta_ms=10.0)

        report2 = monitor.get_periodic_report()
        assert report2 is not None
        assert report2.interval_ticks == 1  # Only the new tick
        assert report2.interval_clipped == 0  # New tick was not clipped

    def test_no_report_for_zero_tick_interval(self):
        """No report if interval elapsed but zero ticks recorded."""
        monitor = LiveClippingMonitor(report_interval_s=0.0)
        # No ticks recorded — just time elapsed
        report = monitor.get_periodic_report()
        assert report is None

    def test_session_totals_unaffected_by_report(self):
        """Session totals persist across periodic report resets."""
        monitor = LiveClippingMonitor(report_interval_s=0.0)
        monitor.record_tick(ms_to_ns(15.0), tick_delta_ms=10.0)

        # Generate report (resets interval)
        monitor.get_periodic_report()

        monitor.record_tick(ms_to_ns(20.0), tick_delta_ms=10.0)

        # Session summary sees ALL ticks
        summary = monitor.get_session_summary()
        assert summary.total_ticks == 2
        assert summary.ticks_clipped == 2
        assert summary.max_stale_ms == 10.0  # max(5, 10)


# ============================================
# Session Summary Edge Cases
# ============================================

class TestSessionSummaryEdgeCases:
    """Edge cases for get_session_summary()."""

    def test_empty_session(self):
        """Summary for session with zero ticks."""
        monitor = LiveClippingMonitor()

        summary = monitor.get_session_summary()
        assert summary.total_ticks == 0
        assert summary.ticks_clipped == 0
        assert summary.clipping_ratio == 0.0
        assert summary.avg_stale_ms == 0.0
        assert summary.avg_processing_ms == 0.0
        assert summary.max_queue_depth == 0
        assert summary.processing_times_ms == []

    def test_all_ticks_clipped(self):
        """100% clipping ratio."""
        monitor = LiveClippingMonitor()
        monitor.record_tick(ms_to_ns(50.0), tick_delta_ms=10.0)
        monitor.record_tick(ms_to_ns(30.0), tick_delta_ms=10.0)

        summary = monitor.get_session_summary()
        assert summary.clipping_ratio == 1.0
        assert summary.ticks_clipped == 2

    def test_no_ticks_clipped(self):
        """0% clipping ratio with many ticks."""
        monitor = LiveClippingMonitor()
        for _ in range(100):
            monitor.record_tick(ms_to_ns(0.1), tick_delta_ms=10.0)

        summary = monitor.get_session_summary()
        assert summary.total_ticks == 100
        assert summary.clipping_ratio == 0.0
        assert summary.avg_stale_ms == 0.0

    def test_single_tick(self):
        """Summary correct for exactly one tick."""
        monitor = LiveClippingMonitor()
        monitor.record_tick(ms_to_ns(3.0), tick_delta_ms=0.0)  # first tick, delta=0

        summary = monitor.get_session_summary()
        assert summary.total_ticks == 1
        assert summary.ticks_clipped == 0
        assert summary.avg_processing_ms == 3.0
        assert summary.max_processing_ms == 3.0


# ============================================
# Strategy
# ============================================

class TestStrategy:
    """Phase 6: Strategy configuration."""

    def test_default_strategy(self):
        """Default strategy is queue_all."""
        monitor = LiveClippingMonitor()
        assert monitor.get_strategy() == 'queue_all'

    def test_custom_strategy(self):
        """Custom strategy is stored correctly."""
        monitor = LiveClippingMonitor(strategy='drop_stale')
        assert monitor.get_strategy() == 'drop_stale'
