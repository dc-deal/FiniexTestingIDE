"""
FiniexTestingIDE - Market Data Status Types
Session-level tick-stream health status (#436) — the market-data analogue of the
per-worker SIGNAL feed envelope (WorkerResult.is_stale, #434).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class MarketDataStatus:
    """
    Session-level market-data health, evaluated by the live tick loop (#436).

    Default = fresh: the sim executor never sets this status — replay gaps are
    data (weekend/holiday), not outages — so downstream readers see a healthy
    feed in backtests by design. Live, the loop's heartbeat evaluates the age
    of the last real tick against execution.market_data_stale_after_s and
    updates this status on every pass.

    Upgrade path (#375): grows into the typed FeedStatus struct on the unified
    event timeline (error states, typed connection events).
    """
    is_stale: bool = False
    stale_since: Optional[datetime] = None
    seconds_since_last_tick: float = 0.0
    reconnect_count: int = 0
