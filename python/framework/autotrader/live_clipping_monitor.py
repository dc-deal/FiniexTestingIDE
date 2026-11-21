"""
FiniexTestingIDE - Live Clipping Monitor
Real-time tick processing measurement and clipping detection (#197).
"""

import time
from collections import deque
from typing import Deque, Optional

from python.framework.types.autotrader_types.clipping_monitor_types import (
    ClippingDisplaySnapshot,
    ClippingReport,
    ClippingSessionSummary,
)

# How many of the most recent processing times the display's percentiles are read from. Bounded
# because the list used to keep every tick of the session: a month-long session would hold
# millions of entries, copy them into every display frame on the tick thread and sort them on
# every render (measured 2026-10-13: 3 M entries cost 30 ms per copy and 728 ms per sort).
PERCENTILE_WINDOW_TICKS = 10_000


class LiveClippingMonitor:
    """
    Monitors pass processing performance and counts clipping in live mode.

    Clipping occurs when the loop is still busy with one pass while ticks arrive: they wait, and
    the next pass takes them as one slice whose newest tick alone the algo computes on (#461) —
    the live side of a backtest's clipping, measured, never simulated.

    Phases (#197, #461):
    - Phase 1: Per-pass processing time (perf_counter_ns)
    - Phase 2: Clipping — the ticks of a slice the algo did not compute on
    - Phase 3: Counters (ticks_clipped, max_stale_ms, avg_stale_ms)
    - Phase 4: Periodic reports (configurable interval)
    - Phase 5: Queue depth monitoring

    Args:
        report_interval_s: Seconds between periodic reports
    """

    def __init__(self, report_interval_s: float = 60.0):
        self._report_interval_s = report_interval_s

        # Session totals
        self._total_ticks: int = 0
        self._total_passes: int = 0
        self._ticks_clipped: int = 0
        self._passes_clipped: int = 0
        self._total_stale_ms: float = 0.0
        self._max_stale_ms: float = 0.0
        self._max_processing_ms: float = 0.0
        self._total_processing_ms: float = 0.0
        self._max_queue_depth: int = 0
        self._processing_times_ms: Deque[float] = deque(maxlen=PERCENTILE_WINDOW_TICKS)

        # Interval tracking (for periodic reports)
        self._interval_ticks: int = 0
        self._interval_passes: int = 0
        self._interval_clipped: int = 0
        self._interval_passes_clipped: int = 0
        self._interval_stale_ms: float = 0.0
        self._interval_max_stale_ms: float = 0.0
        self._interval_max_processing_ms: float = 0.0
        self._interval_total_processing_ms: float = 0.0
        self._interval_max_queue_depth: int = 0
        self._last_report_time: float = time.monotonic()

    def record_pass(self, processing_ns: int, ticks: int, ticks_skipped: int,
                    wait_ms: float) -> None:
        """
        Record one pass over a slice (#461): how long it took, how many ticks it held, and how
        many of them the algo did not compute on.

        Live, a slice the loop fell behind on holds the backlog, and the algo computes once, on
        its newest tick: the ticks of every arrival — one venue message — before the newest waited
        behind the busy loop and are this session's clipped ticks. A replay is never behind and
        never clips.

        Args:
            processing_ns: The pass's processing time in nanoseconds (perf_counter_ns delta)
            ticks: How many ticks the slice held
            ticks_skipped: Of them, how many the algo did not compute on
            wait_ms: How long the slice's oldest arrival waited before the loop took it, in
                milliseconds on the monotonic clock
        """
        processing_ms = processing_ns / 1_000_000.0

        # Phase 1: Store processing time
        self._total_ticks += ticks
        self._interval_ticks += ticks
        self._total_passes += 1
        self._interval_passes += 1
        self._total_processing_ms += processing_ms
        self._interval_total_processing_ms += processing_ms
        self._processing_times_ms.append(processing_ms)

        if processing_ms > self._max_processing_ms:
            self._max_processing_ms = processing_ms
        if processing_ms > self._interval_max_processing_ms:
            self._interval_max_processing_ms = processing_ms

        # Phase 2: Clipping — ticks the algo did not compute on
        if ticks_skipped > 0:
            # Phase 3: Update counters; the staleness is the wait of the slice's oldest tick
            self._ticks_clipped += ticks_skipped
            self._interval_clipped += ticks_skipped
            self._passes_clipped += 1
            self._interval_passes_clipped += 1
            self._total_stale_ms += wait_ms
            self._interval_stale_ms += wait_ms

            if wait_ms > self._max_stale_ms:
                self._max_stale_ms = wait_ms
            if wait_ms > self._interval_max_stale_ms:
                self._interval_max_stale_ms = wait_ms

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
            interval_passes=self._interval_passes,
            interval_clipped=self._interval_clipped,
            interval_max_stale_ms=self._interval_max_stale_ms,
            interval_avg_stale_ms=(
                self._interval_stale_ms / self._interval_passes_clipped
                if self._interval_passes_clipped > 0 else 0.0
            ),
            interval_max_processing_ms=self._interval_max_processing_ms,
            interval_avg_processing_ms=(
                self._interval_total_processing_ms / self._interval_passes
            ),
            interval_max_queue_depth=self._interval_max_queue_depth,
        )

        # Reset interval counters
        self._interval_ticks = 0
        self._interval_passes = 0
        self._interval_clipped = 0
        self._interval_passes_clipped = 0
        self._interval_stale_ms = 0.0
        self._interval_max_stale_ms = 0.0
        self._interval_max_processing_ms = 0.0
        self._interval_total_processing_ms = 0.0
        self._interval_max_queue_depth = 0
        self._last_report_time = now

        return report

    def get_display_stats(self) -> ClippingDisplaySnapshot:
        """
        Lightweight snapshot for live display rendering.

        Avoids full ClippingSessionSummary construction — only the fields
        needed by AutotraderDisplayExporter.build().

        Returns:
            ClippingDisplaySnapshot with current session totals
        """
        total_ticks = self._total_ticks
        total_passes = self._total_passes
        return ClippingDisplaySnapshot(
            total_ticks=total_ticks,
            ticks_clipped=self._ticks_clipped,
            clipping_ratio=self._ticks_clipped / total_ticks if total_ticks > 0 else 0.0,
            avg_processing_ms=(self._total_processing_ms / total_passes
                               if total_passes > 0 else 0.0),
            max_processing_ms=self._max_processing_ms,
            processing_times_ms=list(self._processing_times_ms),
        )

    def get_session_summary(self) -> ClippingSessionSummary:
        """
        Return end-of-session clipping summary.

        Returns:
            ClippingSessionSummary with aggregated totals
        """
        return ClippingSessionSummary(
            total_ticks=self._total_ticks,
            total_passes=self._total_passes,
            ticks_clipped=self._ticks_clipped,
            clipping_ratio=(
                self._ticks_clipped / self._total_ticks
                if self._total_ticks > 0 else 0.0
            ),
            max_stale_ms=self._max_stale_ms,
            avg_stale_ms=(
                self._total_stale_ms / self._passes_clipped
                if self._passes_clipped > 0 else 0.0
            ),
            max_processing_ms=self._max_processing_ms,
            avg_processing_ms=(
                self._total_processing_ms / self._total_passes
                if self._total_passes > 0 else 0.0
            ),
            max_queue_depth=self._max_queue_depth,
        )
