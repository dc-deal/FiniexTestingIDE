"""
Venue API performance report builder (#631) — the REST monitor's postprocessor.

Maps the snapshot a live session's API monitor took at its end onto the
`VenueApiPerformanceReport` model: per endpoint its calls, latency, slow calls and failed calls,
busiest first, with the session's totals and the threshold a call was judged slow against.
AutoTrader only — a backtest calls no venue.
"""

from python.framework.types.api.report_types import (
    VenueApiPerformanceReport,
    VenueEndpointPerformanceRow,
)
from python.framework.types.live_types.api_perf_types import ApiPerfSnapshot


def build_venue_api_performance_report(run_id: str,
                                       snapshot: ApiPerfSnapshot) -> VenueApiPerformanceReport:
    """
    Build the venue API performance report for a session.

    Args:
        run_id: The run this report belongs to
        snapshot: What the session's API monitor held at the session's end

    Returns:
        The report — one row per endpoint the session called, busiest first
    """
    return VenueApiPerformanceReport(
        run_id=run_id,
        slow_call_threshold_ms=snapshot.slow_call_threshold_ms,
        slow_calls=snapshot.slow_count,
        failed_calls=snapshot.total_errors,
        endpoints=[VenueEndpointPerformanceRow(
            venue_endpoint=stats.endpoint, calls=stats.count, avg_ms=stats.avg_ms,
            min_ms=stats.min_ms, max_ms=stats.max_ms, slow_calls=stats.slow_count,
            failed_calls=stats.error_count) for stats in snapshot.endpoints])
