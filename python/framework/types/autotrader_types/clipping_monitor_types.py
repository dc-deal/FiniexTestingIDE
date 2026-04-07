"""
FiniexTestingIDE - Clipping Monitor Types
Data structures for live tick clipping monitoring (#197).
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ClippingReport:
    """
    Periodic clipping report during live session.

    Generated at configurable intervals by LiveClippingMonitor.

    Args:
        interval_ticks: Ticks processed in this interval
        interval_clipped: Ticks that were stale (processing > inter-tick delta)
        interval_max_stale_ms: Maximum staleness in this interval
        interval_avg_stale_ms: Average staleness in this interval
        interval_max_processing_ms: Maximum processing time in this interval
        interval_avg_processing_ms: Average processing time in this interval
        interval_max_queue_depth: Maximum queue depth in this interval
    """
    interval_ticks: int = 0
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
    AutotraderTickLoop._build_display_stats(). Avoids full summary
    construction on every tick.

    Args:
        total_ticks: Total ticks processed so far
        ticks_clipped: Total clipped ticks
        clipping_ratio: Fraction clipped (0.0 to 1.0)
        avg_processing_ms: Average tick processing time
        max_processing_ms: Maximum tick processing time
        processing_times_ms: All processing times (for percentile display)
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
        ticks_clipped: Total ticks that arrived while previous tick was still processing
        clipping_ratio: Fraction of ticks that were clipped (0.0 to 1.0)
        max_stale_ms: Maximum staleness observed across session
        avg_stale_ms: Average staleness of clipped ticks
        max_processing_ms: Maximum per-tick processing time
        avg_processing_ms: Average per-tick processing time
        max_queue_depth: Maximum queue depth observed
        processing_times_ms: All recorded processing times (for percentile analysis)
    """
    total_ticks: int = 0
    ticks_clipped: int = 0
    clipping_ratio: float = 0.0
    max_stale_ms: float = 0.0
    avg_stale_ms: float = 0.0
    max_processing_ms: float = 0.0
    avg_processing_ms: float = 0.0
    max_queue_depth: int = 0
    processing_times_ms: List[float] = field(default_factory=list)
