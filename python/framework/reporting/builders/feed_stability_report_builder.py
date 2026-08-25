"""
Feed stability report builder (#451) — the disturbance-episode postprocessor.

Turns the observed outage episodes of BOTH staleness domains — the tick stream (#436)
and every SIGNAL source (#434) — into one per-source table. The episodes arrive already
measured from the run; this stage only groups them, resolves the source identity a
capture site could not know, and labels their origin.

The origin join is the ONLY place a stress configuration is read, and it contributes the
label alone: an episode's timestamps always come from the observed state change, because
a planned window and the experienced outage differ whenever the run ends inside the
window or the tick stream never enters it.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    FeedStabilityEpisodeRow,
    FeedStabilityReport,
    FeedStabilitySourceRow,
)
from python.framework.types.disturbance_episode_types import (
    DisturbanceDomain,
    DisturbanceEpisode,
    DisturbanceOrigin,
)
from python.framework.types.trading_env_types.stress_test_types import StaleDataEvent

# Fallback identity for a signal episode whose unit binds no named source
UNKNOWN_SIGNAL_SOURCE = '(signal)'


def build_feed_stability_report(units: List[RunUnit]) -> FeedStabilityReport:
    """
    Build the feed-stability report from the run's units.

    Args:
        units: The run's units (sim: N scenarios; live: 1 session)

    Returns:
        FeedStabilityReport with one row per disturbed source (empty when the run had
        no episode at all — a clean run renders no section)
    """
    rows: Dict[Tuple[str, str], FeedStabilitySourceRow] = {}

    for unit in units:
        for episode in unit.disturbance_episodes:
            source = _resolve_source(episode, unit)
            origin, label = _resolve_origin(episode, source, unit.planned_outages)
            row = rows.get((source, episode.domain.value))
            if row is None:
                row = FeedStabilitySourceRow(
                    source=source, domain=episode.domain.value)
                rows[(source, episode.domain.value)] = row

            row.episodes.append(FeedStabilityEpisodeRow(
                unit=episode.unit_name,
                symbol=episode.symbol,
                stale_from=_iso(episode.stale_from),
                stale_to=_iso(episode.stale_to),
                duration_seconds=episode.duration_seconds,
                origin=origin.value,
                label=label,
            ))
            row.stale_seconds += episode.duration_seconds
            row.episode_count += 1
            if origin.value not in row.origins:
                row.origins.append(origin.value)

    # Early exit: a run without a single disturbance renders no section.
    if not rows:
        return FeedStabilityReport(units=[])

    _attach_counters(rows, units)

    ordered = [rows[key] for key in sorted(rows.keys())]
    return FeedStabilityReport(
        units=ordered,
        episode_count=sum(row.episode_count for row in ordered),
        stale_seconds=sum(row.stale_seconds for row in ordered),
        stress_injected_count=sum(
            1 for row in ordered for episode in row.episodes
            if episode.origin == DisturbanceOrigin.STRESS_INJECTED.value),
        source_count=len(ordered),
    )


def _resolve_source(episode: DisturbanceEpisode, unit: RunUnit) -> str:
    """
    The source identity of one episode.

    The tick domain knows its source at capture (the broker type); the signal domain does
    not — a SIGNAL worker knows its payload KIND ('llm_sentiment'), while the report groups
    by the archive source the unit bound ('crypto_sentiment'). Same join the #433 counters
    use; the key gains a source dimension with #258.

    Args:
        episode: The captured episode
        unit: The run unit it was captured in

    Returns:
        The source name to group by
    """
    if episode.source:
        return episode.source
    return unit.sentiment_source or UNKNOWN_SIGNAL_SOURCE


def _resolve_origin(
    episode: DisturbanceEpisode,
    source: str,
    planned_outages: List[StaleDataEvent],
) -> Tuple[DisturbanceOrigin, str]:
    """
    Whether an episode was real or injected, plus the injection's label.

    A capture site that KNOWS it injected (the sim stress driver, the mock tick source's
    drill) has already stamped both. Everything else is matched against the unit's planned
    windows by OVERLAP: a signal outage flips only after max_staleness_minutes have passed
    inside the carved window, so the episode starts later than the window — containment
    would miss it. An episode overlapping a planned window counts as injected.

    Args:
        episode: The captured episode
        source: Its resolved source identity
        planned_outages: The unit's configured stale windows

    Returns:
        Tuple of (origin, label)
    """
    if episode.origin == DisturbanceOrigin.STRESS_INJECTED:
        return episode.origin, episode.label

    # An episode that never recovered reaches to the run end — only its start bounds it.
    end = episode.stale_to
    for event in planned_outages:
        if event.data_source != source:
            continue
        if end is not None and event.stale_start_date >= end:
            continue
        if event.stale_end_date <= episode.stale_from:
            continue
        return DisturbanceOrigin.STRESS_INJECTED, event.label

    return DisturbanceOrigin.LIVE_REAL, episode.label


def _attach_counters(
    rows: Dict[Tuple[str, str], FeedStabilitySourceRow],
    units: List[RunUnit],
) -> None:
    """
    Add the per-tick counters of each domain to its source row.

    Tick domain: the market-data twin (#451 Part 4). Signal domain: the #433 Part C
    counters, summed over the unit's SIGNAL workers on that source. Counters are summed
    across units, so a batch reports one figure per source.

    Args:
        rows: The source rows built from the episodes (mutated in place)
        units: The run's units
    """
    for unit in units:
        stats = unit.market_data_tick_stats
        if stats is not None:
            tick_row = rows.get((stats.source, DisturbanceDomain.TICK.value))
            if tick_row is not None:
                tick_row.fresh_ticks += stats.fresh_ticks
                tick_row.stale_ticks += stats.stale_ticks

        signal_row = rows.get(
            (unit.sentiment_source or UNKNOWN_SIGNAL_SOURCE,
             DisturbanceDomain.SIGNAL.value))
        if signal_row is None:
            continue
        for signal_stats in unit.signal_statistics:
            signal_row.fresh_ticks += signal_stats.fresh_ticks
            signal_row.stale_ticks += signal_stats.stale_ticks
            signal_row.blind_ticks += signal_stats.blind_ticks


def _iso(moment: Optional[datetime]) -> str:
    """ISO-8601 string of a UTC moment, '' when absent."""
    return moment.isoformat() if moment is not None else ''
