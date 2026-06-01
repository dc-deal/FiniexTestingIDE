"""
FiniexTestingIDE - API Performance Types (#351)
Domain types for the broker REST transport-latency monitor: per-endpoint
latency/error aggregates and the slim snapshot consumed by the live display.

Experimental, by design — aggregate per-endpoint metrics. A rigorous view wants
the full return-speed distribution per endpoint over time; this is the pragmatic
first cut (count / avg / min / max / errors + threshold logging).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class ApiEndpointStats:
    """
    Running latency + error aggregates for one broker REST endpoint.

    One instance per distinct endpoint; updated in place on every call (the live
    panel renders one row per endpoint, not one per call).

    Args:
        endpoint: Endpoint identifier (e.g. '/0/private/OpenOrders')
        count: Number of calls recorded
        last_fired_at: Timestamp of the most recent call (UTC, tz-aware)
        last_ms: Duration of the most recent call (milliseconds)
        min_ms: Fastest observed call (milliseconds)
        max_ms: Slowest observed call (milliseconds)
        total_ms: Cumulative duration — avg = total_ms / count
        error_count: Failed calls (Kraken `error` responses + transport failures)
        last_error: Message of the most recent failure (None if never failed)
    """
    endpoint: str
    count: int = 0
    last_fired_at: Optional[datetime] = None
    last_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    total_ms: float = 0.0
    error_count: int = 0
    last_error: Optional[str] = None

    @property
    def avg_ms(self) -> float:
        """Average call duration in milliseconds (0.0 before the first call)."""
        return self.total_ms / self.count if self.count else 0.0


@dataclass
class ApiPerfSnapshot:
    """
    Immutable per-display snapshot of the monitor state.

    Args:
        endpoints: Per-endpoint stats, ordered by call count (busiest first)
        slow_count: Calls slower than the configured threshold this session
        total_errors: Failed calls across all endpoints this session
    """
    endpoints: List[ApiEndpointStats] = field(default_factory=list)
    slow_count: int = 0
    total_errors: int = 0
