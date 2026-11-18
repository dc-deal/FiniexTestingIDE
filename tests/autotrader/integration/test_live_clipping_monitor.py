"""
LiveClippingMonitor — Unit Tests

Tests the clipping count (#461: the ticks of a slice the algo did not compute on), counter
accuracy, periodic reports, session summaries, queue depth tracking, and edge cases.

All tests use controlled inputs (no real ticks, no time dependency).
"""

from unittest.mock import patch

from python.framework.autotrader.live_clipping_monitor import (
    PERCENTILE_WINDOW_TICKS,
    LiveClippingMonitor,
)

# --- Helpers ---

def ms_to_ns(ms: float) -> int:
    """Convert milliseconds to nanoseconds for record_pass() input."""
    return int(ms * 1_000_000)


# ============================================
# Clipping
# ============================================

class TestClipping:
    """
    Clipping is what a backlog leaves behind (#461): the ticks of a slice the algo did not compute
    on, because they arrived while a pass was still running and waited behind a newer arrival.
    """

    def test_a_pass_without_a_backlog_clips_nothing(self):
        monitor = LiveClippingMonitor()
        monitor.record_pass(ms_to_ns(0.5), ticks=1, ticks_skipped=0, wait_ms=0.0)

        summary = monitor.get_session_summary()
        assert summary.ticks_clipped == 0
        assert summary.clipping_ratio == 0.0
        assert summary.max_stale_ms == 0.0

    def test_the_ticks_behind_the_newest_are_clipped(self):
        """A backlog of four ticks: the algo computed on the newest, three waited for it."""
        monitor = LiveClippingMonitor()
        monitor.record_pass(ms_to_ns(15.0), ticks=4, ticks_skipped=3, wait_ms=12.0)

        summary = monitor.get_session_summary()
        assert (summary.total_ticks, summary.total_passes) == (4, 1)
        assert summary.ticks_clipped == 3
        assert summary.clipping_ratio == 0.75, 'ticks against ticks'
        assert summary.max_stale_ms == 12.0

    def test_the_ticks_of_one_arrival_are_not_clipped(self):
        """A frame of three trades arrives at once: one slice, nothing waited behind a busy loop."""
        monitor = LiveClippingMonitor()
        monitor.record_pass(ms_to_ns(1.0), ticks=3, ticks_skipped=0, wait_ms=0.0)

        summary = monitor.get_session_summary()
        assert summary.total_ticks == 3
        assert summary.ticks_clipped == 0

    def test_a_late_tick_the_algo_still_computed_on_is_not_clipped(self):
        """It waited, but it is the newest of its slice: the decision ran on it."""
        monitor = LiveClippingMonitor()
        monitor.record_pass(ms_to_ns(1.0), ticks=1, ticks_skipped=0, wait_ms=40.0)

        summary = monitor.get_session_summary()
        assert summary.ticks_clipped == 0
        assert summary.max_stale_ms == 0.0


# ============================================
# Counter Accuracy
# ============================================

class TestCounterAccuracy:
    """Verifies counters, averages, and max tracking over multiple passes."""

    def test_mixed_clipping_sequence(self):
        """Passes with and without a backlog produce correct counts."""
        monitor = LiveClippingMonitor()
        monitor.record_pass(ms_to_ns(0.5), ticks=1, ticks_skipped=0, wait_ms=0.0)
        monitor.record_pass(ms_to_ns(12.0), ticks=3, ticks_skipped=2, wait_ms=8.0)
        monitor.record_pass(ms_to_ns(8.0), ticks=1, ticks_skipped=0, wait_ms=0.0)
        monitor.record_pass(ms_to_ns(25.0), ticks=5, ticks_skipped=4, wait_ms=20.0)

        summary = monitor.get_session_summary()
        assert (summary.total_ticks, summary.total_passes) == (10, 4)
        assert summary.ticks_clipped == 6
        assert summary.clipping_ratio == 0.6
        assert summary.max_stale_ms == 20.0
        # The average wait over the two passes that clipped: (8 + 20) / 2
        assert summary.avg_stale_ms == 14.0

    def test_max_processing_tracked(self):
        """Max processing time tracked across all passes."""
        monitor = LiveClippingMonitor()
        for ms in (1.0, 5.0, 3.0):
            monitor.record_pass(ms_to_ns(ms), ticks=1, ticks_skipped=0, wait_ms=0.0)

        summary = monitor.get_session_summary()
        assert summary.max_processing_ms == 5.0

    def test_avg_processing_is_per_pass(self):
        """A pass over several ticks is one processing time, not one per tick."""
        monitor = LiveClippingMonitor()
        monitor.record_pass(ms_to_ns(2.0), ticks=1, ticks_skipped=0, wait_ms=0.0)
        monitor.record_pass(ms_to_ns(4.0), ticks=3, ticks_skipped=2, wait_ms=1.0)
        monitor.record_pass(ms_to_ns(6.0), ticks=2, ticks_skipped=0, wait_ms=0.0)

        summary = monitor.get_session_summary()
        assert summary.total_ticks == 6
        assert summary.avg_processing_ms == 4.0
        assert monitor.get_display_stats().avg_processing_ms == 4.0

    def test_processing_times_list_recorded(self):
        """The individual processing times reach the display snapshot, oldest first."""
        monitor = LiveClippingMonitor()
        for ms in (1.5, 2.5, 3.5):
            monitor.record_pass(ms_to_ns(ms), ticks=1, ticks_skipped=0, wait_ms=0.0)

        assert monitor.get_display_stats().processing_times_ms == [1.5, 2.5, 3.5]

    def test_the_percentile_window_keeps_only_the_most_recent_ticks(self):
        """
        The list behind the display's percentiles is bounded, the session's totals are not.

        It used to keep every tick of the session, so a month-long session copied millions of
        entries into each display frame and sorted them on each render.
        """
        monitor = LiveClippingMonitor()
        overflow = 5
        for index in range(PERCENTILE_WINDOW_TICKS + overflow):
            monitor.record_pass(ms_to_ns(float(index)), ticks=1, ticks_skipped=0, wait_ms=0.0)

        window = monitor.get_display_stats().processing_times_ms
        assert len(window) == PERCENTILE_WINDOW_TICKS
        assert window[0] == float(overflow)
        assert window[-1] == float(PERCENTILE_WINDOW_TICKS + overflow - 1)

        summary = monitor.get_session_summary()
        assert summary.total_ticks == PERCENTILE_WINDOW_TICKS + overflow
        assert summary.max_processing_ms == float(PERCENTILE_WINDOW_TICKS + overflow - 1)


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
        monitor.record_pass(ms_to_ns(1.0), ticks=1, ticks_skipped=0, wait_ms=0.0)

        report = monitor.get_periodic_report()
        assert report is None

    def test_report_after_interval(self):
        """get_periodic_report() returns report after interval elapses."""
        monitor = LiveClippingMonitor(report_interval_s=1.0)
        monitor.record_pass(ms_to_ns(1.0), ticks=1, ticks_skipped=0, wait_ms=0.0)
        monitor.record_pass(ms_to_ns(15.0), ticks=3, ticks_skipped=2, wait_ms=5.0)
        monitor.record_queue_depth(4)

        # Fast-forward monotonic clock past interval
        with patch('python.framework.autotrader.live_clipping_monitor.time.monotonic',
                   return_value=monitor._last_report_time + 2.0):
            report = monitor.get_periodic_report()

        assert report is not None
        assert (report.interval_ticks, report.interval_passes) == (4, 2)
        assert report.interval_clipped == 2
        assert report.interval_max_stale_ms == 5.0
        assert report.interval_avg_stale_ms == 5.0
        assert report.interval_max_processing_ms == 15.0
        assert report.interval_avg_processing_ms == 8.0  # (1+15)/2 passes
        assert report.interval_max_queue_depth == 4

    def test_interval_counters_reset_after_report(self):
        """Interval counters reset to zero after report generation."""
        monitor = LiveClippingMonitor(report_interval_s=0.0)
        monitor.record_pass(ms_to_ns(20.0), ticks=2, ticks_skipped=1, wait_ms=10.0)

        # Force interval elapsed (interval_s=0 → always elapsed)
        report = monitor.get_periodic_report()
        assert report is not None
        assert report.interval_ticks == 2

        # Record another pass — new interval
        monitor.record_pass(ms_to_ns(2.0), ticks=1, ticks_skipped=0, wait_ms=0.0)

        report2 = monitor.get_periodic_report()
        assert report2 is not None
        assert (report2.interval_ticks, report2.interval_passes) == (1, 1)  # Only the new pass
        assert report2.interval_clipped == 0  # The new pass had no backlog

    def test_no_report_for_zero_tick_interval(self):
        """No report if interval elapsed but zero ticks recorded."""
        monitor = LiveClippingMonitor(report_interval_s=0.0)
        # No ticks recorded — just time elapsed
        report = monitor.get_periodic_report()
        assert report is None

    def test_session_totals_unaffected_by_report(self):
        """Session totals persist across periodic report resets."""
        monitor = LiveClippingMonitor(report_interval_s=0.0)
        monitor.record_pass(ms_to_ns(15.0), ticks=2, ticks_skipped=1, wait_ms=5.0)

        # Generate report (resets interval)
        monitor.get_periodic_report()

        monitor.record_pass(ms_to_ns(20.0), ticks=3, ticks_skipped=2, wait_ms=10.0)

        # Session summary sees ALL passes
        summary = monitor.get_session_summary()
        assert (summary.total_ticks, summary.total_passes) == (5, 2)
        assert summary.ticks_clipped == 3
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
        assert (summary.total_ticks, summary.total_passes) == (0, 0)
        assert summary.ticks_clipped == 0
        assert summary.clipping_ratio == 0.0
        assert summary.avg_stale_ms == 0.0
        assert summary.avg_processing_ms == 0.0
        assert summary.max_queue_depth == 0
        assert monitor.get_display_stats().processing_times_ms == []

    def test_every_tick_but_the_newest_clipped(self):
        """One pass over a backlog of ten: nine clipped, the ratio never reaches 1."""
        monitor = LiveClippingMonitor()
        monitor.record_pass(ms_to_ns(50.0), ticks=10, ticks_skipped=9, wait_ms=45.0)

        summary = monitor.get_session_summary()
        assert summary.ticks_clipped == 9
        assert summary.clipping_ratio == 0.9

    def test_no_ticks_clipped(self):
        """0% clipping ratio with many passes."""
        monitor = LiveClippingMonitor()
        for _ in range(100):
            monitor.record_pass(ms_to_ns(0.1), ticks=1, ticks_skipped=0, wait_ms=0.0)

        summary = monitor.get_session_summary()
        assert summary.total_ticks == 100
        assert summary.clipping_ratio == 0.0
        assert summary.avg_stale_ms == 0.0

    def test_single_tick(self):
        """Summary correct for exactly one tick."""
        monitor = LiveClippingMonitor()
        monitor.record_pass(ms_to_ns(3.0), ticks=1, ticks_skipped=0, wait_ms=0.0)

        summary = monitor.get_session_summary()
        assert summary.total_ticks == 1
        assert summary.ticks_clipped == 0
        assert summary.avg_processing_ms == 3.0
        assert summary.max_processing_ms == 3.0
