"""
FiniexTestingIDE - Clipping Monitor Types
Data structures for live tick clipping monitoring (#197).
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ClippingReport:
    """
    Periodic clipping report during an AutoTrader session.

    Generated at configurable intervals by LiveClippingMonitor.

    Args:
        interval_ticks: Ticks processed in this interval
        interval_passes: Passes in this interval, each over one slice of ticks
        interval_clipped: Ticks of the arrivals before a slice's newest: they waited behind a busy
            loop, and the algo did not compute on them
        interval_max_stale_ms: Longest wait of a clipped slice's oldest arrival in this interval
        interval_avg_stale_ms: Average wait of a clipped slice's oldest arrival in this interval
        interval_max_processing_ms: Maximum processing time of a pass in this interval
        interval_avg_processing_ms: Average processing time of a pass in this interval
        interval_max_queue_depth: Maximum tick queue depth in this interval, in items, not
            ticks: one per arrival (a venue message live, an arrival millisecond in a replay)
            and one per venue answer whose wake was not yet taken
    """
    interval_ticks: int = 0
    interval_passes: int = 0
    interval_clipped: int = 0
    interval_max_stale_ms: float = 0.0
    interval_avg_stale_ms: float = 0.0
    interval_max_processing_ms: float = 0.0
    interval_avg_processing_ms: float = 0.0
    interval_max_queue_depth: int = 0


@dataclass
class ClippingDisplaySnapshot:
    """
    Lightweight clipping stats for live display rendering.

    Subset of ClippingSessionSummary — only the fields needed by
    AutotraderDisplayExporter.build(). Avoids full summary
    construction on every tick.

    Args:
        total_ticks: Total ticks processed so far
        ticks_clipped: Total clipped ticks — ticks the algo did not compute on
        clipping_ratio: Fraction of the ticks clipped (0.0 to 1.0)
        avg_processing_ms: Average processing time of a pass
        max_processing_ms: Maximum processing time of a pass
        processing_times_ms: The most recent processing times, at most PERCENTILE_WINDOW_TICKS
            of them — the display reads its percentiles from this window, not the session
    """
    total_ticks: int = 0
    ticks_clipped: int = 0
    clipping_ratio: float = 0.0
    avg_processing_ms: float = 0.0
    max_processing_ms: float = 0.0
    processing_times_ms: List[float] = field(default_factory=list)


@dataclass
class ClippingSessionSummary:
    """
    End-of-session clipping summary.

    Aggregated totals across the entire AutoTrader session.

    Args:
        total_ticks: Total ticks processed
        total_passes: Total passes, each over one slice of ticks (#461)
        ticks_clipped: Total ticks of the arrivals before a slice's newest: they waited behind a
            busy loop, and the algo did not compute on them
        clipping_ratio: Fraction of ticks that were clipped (0.0 to 1.0)
        max_stale_ms: Longest a clipped slice's oldest arrival waited before the loop took it
        avg_stale_ms: Average of that wait over the slices that clipped
        max_processing_ms: Maximum processing time of a pass
        avg_processing_ms: Average processing time of a pass
        max_queue_depth: Maximum tick queue depth observed, in items, not ticks: one per
            arrival (a venue message live, an arrival millisecond in a replay) and one per venue
            answer whose wake was not yet taken
    """
    total_ticks: int = 0
    total_passes: int = 0
    ticks_clipped: int = 0
    clipping_ratio: float = 0.0
    max_stale_ms: float = 0.0
    avg_stale_ms: float = 0.0
    max_processing_ms: float = 0.0
    avg_processing_ms: float = 0.0
    max_queue_depth: int = 0
