"""
FiniexTestingIDE - Live Clipping Monitor
Real-time tick processing measurement and clipping detection (#197).
"""

import time
from typing import Optional

from python.framework.types.autotrader_types.clipping_monitor_types import ClippingReport, ClippingSessionSummary


class LiveClippingMonitor:
    """
    Monitors tick processing performance and detects clipping in live mode.

    Clipping occurs when tick processing time exceeds the inter-tick arrival
    interval — meaning the next tick arrives before the current one is done.

    Phases (#197):
    - Phase 1: Per-tick processing time (perf_counter_ns)
    - Phase 2: Clipping detection (processing_time vs tick_delta)
    - Phase 3: Counters (ticks_clipped, max_stale_ms, avg_stale_ms)
    - Phase 4: Periodic reports (configurable interval)
    - Phase 5: Queue depth monitoring
    - Phase 6: Strategy (queue_all / drop_stale)

    Args:
        report_interval_s: Seconds between periodic reports
        strategy: Clipping strategy ('queue_all' or 'drop_stale')
    """

    def __init__(self, report_interval_s: float = 60.0, strategy: str = 'queue_all'):
        self._report_interval_s = report_interval_s
        self._strategy = strategy

        # Session totals
        self._total_ticks: int = 0
        self._ticks_clipped: int = 0
        self._total_stale_ms: float = 0.0
        self._max_stale_ms: float = 0.0
        self._max_processing_ms: float = 0.0
        self._total_processing_ms: float = 0.0
        self._max_queue_depth: int = 0
        self._processing_times_ms: list = []

        # Interval tracking (for periodic reports)
        self._interval_ticks: int = 0
        self._interval_clipped: int = 0
        self._interval_stale_ms: float = 0.0
        self._interval_max_stale_ms: float = 0.0
        self._interval_max_processing_ms: float = 0.0
        self._interval_total_processing_ms: float = 0.0
        self._interval_max_queue_depth: int = 0
        self._last_report_time: float = time.monotonic()

    def record_tick(self, processing_ns: int, tick_delta_ms: float) -> None:
        """
        Record processing time for a single tick.

        Args:
            processing_ns: Processing time in nanoseconds (perf_counter_ns delta)
            tick_delta_ms: Time since previous tick in milliseconds (collected_msc delta)
        """
        processing_ms = processing_ns / 1_000_000.0

        # Phase 1: Store processing time
        self._total_ticks += 1
        self._interval_ticks += 1
        self._total_processing_ms += processing_ms
        self._interval_total_processing_ms += processing_ms
        self._processing_times_ms.append(processing_ms)

        if processing_ms > self._max_processing_ms:
            self._max_processing_ms = processing_ms
        if processing_ms > self._interval_max_processing_ms:
            self._interval_max_processing_ms = processing_ms

        # Phase 2: Clipping detection
        if tick_delta_ms > 0 and processing_ms > tick_delta_ms:
            stale_ms = processing_ms - tick_delta_ms

            # Phase 3: Update counters
            self._ticks_clipped += 1
            self._interval_clipped += 1
            self._total_stale_ms += stale_ms
            self._interval_stale_ms += stale_ms

            if stale_ms > self._max_stale_ms:
                self._max_stale_ms = stale_ms
            if stale_ms > self._interval_max_stale_ms:
                self._interval_max_stale_ms = stale_ms

    def record_queue_depth(self, depth: int) -> None:
        """
        Record current queue depth (Phase 5).

        Args:
            depth: Current queue.Queue.qsize()
        """
        if depth > self._max_queue_depth:
            self._max_queue_depth = depth
        if depth > self._interval_max_queue_depth:
            self._interval_max_queue_depth = depth

    def get_periodic_report(self) -> Optional[ClippingReport]:
        """
        Return a periodic clipping report if the interval has elapsed.

        Returns:
            ClippingReport if interval elapsed, None otherwise
        """
        now = time.monotonic()
        if now - self._last_report_time < self._report_interval_s:
            return None

        if self._interval_ticks == 0:
            self._last_report_time = now
            return None

        report = ClippingReport(
            interval_ticks=self._interval_ticks,
            interval_clipped=self._interval_clipped,
            interval_max_stale_ms=self._interval_max_stale_ms,
            interval_avg_stale_ms=(
                self._interval_stale_ms / self._interval_clipped
                if self._interval_clipped > 0 else 0.0
            ),
            interval_max_processing_ms=self._interval_max_processing_ms,
            interval_avg_processing_ms=(
                self._interval_total_processing_ms / self._interval_ticks
            ),
            interval_max_queue_depth=self._interval_max_queue_depth,
        )

        # Reset interval counters
        self._interval_ticks = 0
        self._interval_clipped = 0
        self._interval_stale_ms = 0.0
        self._interval_max_stale_ms = 0.0
        self._interval_max_processing_ms = 0.0
        self._interval_total_processing_ms = 0.0
        self._interval_max_queue_depth = 0
        self._last_report_time = now

        return report

    def get_session_summary(self) -> ClippingSessionSummary:
        """
        Return end-of-session clipping summary.

        Returns:
            ClippingSessionSummary with aggregated totals
        """
        return ClippingSessionSummary(
            total_ticks=self._total_ticks,
            ticks_clipped=self._ticks_clipped,
            clipping_ratio=(
                self._ticks_clipped / self._total_ticks
                if self._total_ticks > 0 else 0.0
            ),
            max_stale_ms=self._max_stale_ms,
            avg_stale_ms=(
                self._total_stale_ms / self._ticks_clipped
                if self._ticks_clipped > 0 else 0.0
            ),
            max_processing_ms=self._max_processing_ms,
            avg_processing_ms=(
                self._total_processing_ms / self._total_ticks
                if self._total_ticks > 0 else 0.0
            ),
            max_queue_depth=self._max_queue_depth,
            processing_times_ms=self._processing_times_ms,
        )

    def get_strategy(self) -> str:
        """
        Return the configured clipping strategy.

        Returns:
            'queue_all' or 'drop_stale'
        """
        return self._strategy
