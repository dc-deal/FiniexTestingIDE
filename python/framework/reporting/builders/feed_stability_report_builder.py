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

from typing import List, Tuple

from python.framework.reporting.aggregation.aggregation_fold import (
    fold_group,
    fold_keyed,
    fold_rows,
)
from python.framework.reporting.aggregation.tables.feed_stability_figure_table import (
    FEED_COUNTER_TABLE,
    FEED_SOURCE_TABLE,
    FEED_TOTALS_TABLE,
)
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    FeedStabilityReport,
    FeedStabilitySourceRow,
)
from python.framework.types.disturbance_episode_types import (
    DisturbanceDomain,
    DisturbanceEpisode,
    DisturbanceOrigin,
    FeedCountersOfSource,
    FeedEpisodeInSource,
)
from python.framework.types.trading_env_types.stress_test_types import StaleDataEvent
from python.framework.utils.time_utils import format_iso_or_empty

# Fallback identity for a signal episode whose unit binds no named source
UNKNOWN_SIGNAL_SOURCE = '(signal)'


def build_feed_stability_report(run_id: str, units: List[RunUnit]) -> FeedStabilityReport:
    """
    Build the feed-stability report from the run's units.

    Args:
        run_id: The run this report belongs to
        units: The run's units (sim: N scenarios; live: 1 session)

    Returns:
        FeedStabilityReport with one row per disturbed source (empty when the run had
        no episode at all — a clean run renders no section)
    """
    # One fold row per episode, with the source and origin resolved against its unit
    episodes: List[FeedEpisodeInSource] = []
    for unit in units:
        for episode in unit.disturbance_episodes:
            source = _resolve_source(episode, unit)
            origin, label = _resolve_origin(episode, source, unit.planned_outages)
            episodes.append(FeedEpisodeInSource(
                source=source, domain=episode.domain.value, unit=episode.unit_name,
                symbol=episode.symbol, stale_from=format_iso_or_empty(episode.stale_from),
                stale_to=format_iso_or_empty(episode.stale_to),
                duration_seconds=episode.duration_seconds, origin=origin.value, label=label))

    # Early exit: a run without a single disturbance renders no section.
    if not episodes:
        return FeedStabilityReport(run_id=run_id, units=[])

    # The units' counters, joined onto the rows the episodes created — a unit's counters on a
    # source with no episode have no row to join
    counters = dict(fold_keyed(_counter_rows(units), FEED_COUNTER_TABLE))
    ordered = [FeedStabilitySourceRow(**figures,
                                      **counters.get((figures['source'], figures['domain']), {}))
               for figures in fold_rows(episodes, FEED_SOURCE_TABLE)]
    return FeedStabilityReport(
        run_id=run_id,
        units=ordered,
        **fold_group(ordered, FEED_TOTALS_TABLE),
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
    return unit.data_sentiment_type or UNKNOWN_SIGNAL_SOURCE


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
        if event.stale_data_source != source:
            continue
        if end is not None and event.stale_start_date >= end:
            continue
        if event.stale_end_date <= episode.stale_from:
            continue
        return DisturbanceOrigin.STRESS_INJECTED, event.label

    return DisturbanceOrigin.LIVE_REAL, episode.label


def _counter_rows(units: List[RunUnit]) -> List[FeedCountersOfSource]:
    """
    Each unit's per-tick counters, one row per source and domain they were taken on.

    Tick domain: the market-data twin (#451 Part 4). Signal domain: the #433 Part C counters, one
    row per SIGNAL worker on the unit's source. Summed across units by their table, so a batch
    reports one figure per source.

    Args:
        units: The run's units

    Returns:
        The counter rows
    """
    rows: List[FeedCountersOfSource] = []
    for unit in units:
        stats = unit.market_data_tick_stats
        if stats is not None:
            rows.append(FeedCountersOfSource(
                source=stats.source, domain=DisturbanceDomain.TICK.value,
                fresh_ticks=stats.fresh_ticks, stale_ticks=stats.stale_ticks, blind_ticks=0))
        signal_source = unit.data_sentiment_type or UNKNOWN_SIGNAL_SOURCE
        for signal_stats in unit.signal_statistics:
            rows.append(FeedCountersOfSource(
                source=signal_source, domain=DisturbanceDomain.SIGNAL.value,
                fresh_ticks=signal_stats.fresh_ticks, stale_ticks=signal_stats.stale_ticks,
                blind_ticks=signal_stats.blind_ticks))
    return rows
