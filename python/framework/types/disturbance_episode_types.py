"""
FiniexTestingIDE - Disturbance Episode Types
Typed record of one data-feed outage span, shared by both staleness domains (#451).

Staleness is countable via the per-tick counters (#433 Part C / the market-data twin
below), but a count cannot say WHEN and HOW OFTEN — one long outage and forty short
hiccups produce the same ratio. An episode carries the span, so a run becomes readable.

The episode ALWAYS comes from the observed state change: a stress configuration
contributes the label only, never the timestamps (a planned window and the experienced
one differ whenever the run ends inside the window or no tick falls into it).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class DisturbanceDomain(str, Enum):
    """Which staleness domain an episode belongs to (#436 tick stream / #434 signal)."""
    TICK = 'tick'        # the market-data stream itself (session-level)
    SIGNAL = 'signal'    # one SIGNAL source (per-worker external data)


class DisturbanceOrigin(str, Enum):
    """Whether an episode happened for real or was injected by the fault engine."""
    LIVE_REAL = 'live-real'
    STRESS_INJECTED = 'stress-injected'


@dataclass
class DisturbanceEpisode:
    """
    One observed outage span of one data source.

    Timestamps are stamped from the canonical clock; `duration_seconds` is the MEASURED
    duration (live: wall axis, the physical silence; sim: canonical tick axis). In a mock
    replay session the canonical clock is bimodal (replay tick time vs wall heartbeat time,
    the recorded #436 residue), so `stale_to - stale_from` can differ from the measured
    duration there — the duration is the authoritative figure.

    Args:
        source: The data source that went silent (broker type / signal source)
        domain: Which staleness domain this episode belongs to
        stale_from: When the source was observed to go stale
        stale_to: When it recovered; None = never recovered (still open at run end)
        duration_seconds: Measured outage duration
        origin: Real outage or injected by the stress module
        label: The stress event's label (empty for a real outage)
        unit_name: The run unit that observed it (sim scenario / AutoTrader session)
        symbol: The unit's symbol
    """
    source: str
    domain: DisturbanceDomain
    stale_from: datetime
    stale_to: Optional[datetime]
    duration_seconds: float
    origin: DisturbanceOrigin = DisturbanceOrigin.LIVE_REAL
    label: str = ''
    unit_name: str = ''
    symbol: str = ''


@dataclass
class MarketDataTickStats:
    """
    Per-tick market-data resolution over a run (#451 Part 4) — the tick-domain twin of
    SignalResolutionStats, so the stability table has the same shape in both domains.

    Counted on the algo path (the same basis as the signal counters), so both domains
    describe the same ticks. There is no `blind` class here: a tick stream either delivers
    or it is silent, and silence is measured as staleness of the last known tick.

    Args:
        source: The tick source (broker type)
        fresh_ticks: Ticks decided on while the market data was fresh
        stale_ticks: Ticks decided on while the market data was flagged stale
    """
    source: str = ''
    fresh_ticks: int = 0
    stale_ticks: int = 0


@dataclass(frozen=True)
class FeedEpisodeInSource:
    """
    One disturbance episode as the report serves it, with the source and the domain it belongs to —
    the row a feed-stability source row folds from (#557). The served episode row carries neither:
    they belong to its source row.

    Args:
        source: The disturbed source
        domain: Its staleness domain, by value
        unit: The run unit the episode occurred in
        symbol: The symbol
        stale_from: Where the staleness began, ISO-8601
        stale_to: Where it ended, ISO-8601, '' while open
        duration_seconds: How long it lasted
        origin: Where it came from, by value
        label: Its stress label, '' for an observed one
    """
    source: str
    domain: str
    unit: str
    symbol: str
    stale_from: str
    stale_to: str
    duration_seconds: float
    origin: str
    label: str


@dataclass(frozen=True)
class FeedCountersOfSource:
    """
    One unit's per-tick counters on one source and domain — the row a feed-stability source row's
    counters fold from (#557).

    Args:
        source: The source the counters were taken on
        domain: Its staleness domain, by value
        fresh_ticks: Ticks decided on fresh data
        stale_ticks: Ticks decided on stale data
        blind_ticks: Ticks with no data to decide on — 0 in the tick domain
    """
    source: str
    domain: str
    fresh_ticks: int
    stale_ticks: int
    blind_ticks: int
