"""
Signal report builder (#433) — the signal-configuration postprocessor.

Joins the two planes a signal source has:
- ARCHIVE (what the source could offer): provenance, measured cadence and gaps, read from
  the SignalCoverageReport that the shared data preparation built in Phase 1 (#447).
- RUNTIME (what the strategy actually decided on): the per-tick fresh/stale/blind counters
  the SIGNAL workers captured (#433 Part C), carried on the RunUnits.

Unified across both pipelines: sim batch and mock session run through the SAME
MountPreparer, so both hand in the same signal scenario map and one entry point serves both.
Reads the map directly — NOT via RunUnit — because it is a source-keyed data snapshot, not a
per-unit record (same pattern as the broker section).
"""

from typing import Dict, List, Optional, Tuple

from python.framework.discoveries.signal_coverage.signal_coverage_report import SignalCoverageReport
from python.framework.discoveries.signal_coverage.signal_scenario_info import SignalScenarioInfo
from python.framework.reporting.aggregation.aggregation_fold import (
    fold_group,
    fold_rows,
    served_part,
)
from python.framework.reporting.aggregation.tables.signal_figure_table import (
    FEED_PROVENANCE_TABLE,
    SIGNAL_COVERAGE_TABLE,
    SIGNAL_RESOLUTION_TABLE,
)
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import SignalReport, SignalSourceRow, SignalUsageRow
from python.framework.types.scenario_types.scenario_set_types import (
    SignalScenarioUsage,
)
from python.framework.types.signal_data_types import (
    SignalObservedSeries,
    SignalResolutionStats,
    SignalSeriesKind,
    SignalStatsOfUnit,
)
from python.framework.utils.time_utils import format_iso_or_empty


def build_signal_report(
    run_id: str,
    signal_scenario_map: Dict[Tuple[str, str], SignalScenarioInfo],
    units: List[RunUnit],
    observed_feed: Optional[SignalObservedSeries] = None,
) -> SignalReport:
    """
    Build the signal report from the prepared signal map + the run's units.

    Two entry paths, because a signal source reaches a run two ways. A scenario map means
    an archive was prepared and analysed — the sim batch and the mock session.
    An observed feed means the envelopes arrived while the session ran, which is the
    live-adapter case: there is no archive to analyse, only what the feed stated about itself.

    Args:
        run_id: The run this report belongs to
        signal_scenario_map: (source, symbol) → coverage + the scenario windows bound to it
        units: The run's units (sim: N scenarios; live: 1 session) — carry the counters
        observed_feed: What a live transport accumulated, when this run consumed one

    Returns:
        SignalReport with one unit per signal source (empty when no source is bound)
    """
    # Early exit: this run bound no signal source at all — neither archive nor feed
    if not signal_scenario_map and observed_feed is None:
        return SignalReport(run_id=run_id, units=[])

    if not signal_scenario_map:
        return SignalReport(run_id=run_id, units=[_to_feed_row(observed_feed, units)])

    stats_index = _index_resolution_stats(units)

    # Group by source — one console block per source, its symbols folded into the usages.
    by_source: Dict[str, List[SignalScenarioInfo]] = {}
    for info in signal_scenario_map.values():
        by_source.setdefault(info.data_sentiment_type, []).append(info)

    rows = [
        _to_source_row(source, infos, stats_index)
        for source, infos in sorted(by_source.items())
    ]
    return SignalReport(run_id=run_id, units=rows)


def _index_resolution_stats(
    units: List[RunUnit],
) -> Dict[Tuple[str, str], SignalResolutionStats]:
    """
    Index the per-worker counters by (unit name, symbol).

    Deliberately NOT keyed by the worker's CONSUMED_SIGNAL_KIND: that names the payload
    kind ('llm_sentiment'), while the report groups by the archive source the scenario
    binds ('crypto_sentiment_mock' — the pipeline_id). A scenario binds exactly ONE signal
    source today, so the unit identifies it unambiguously — id-addressed multi-source
    binding is #258, which is where this key gains the source dimension.

    A unit may run several SIGNAL workers on that one source (e.g. different staleness
    thresholds); their TICK counters are summed, because the report's granularity is the
    scenario's use of a source, not the worker instance.

    The clamp counters are the exception and merge by MAX. They describe the SERIES, and
    those workers share ONE provider — so summing them would multiply a single producer
    clock correction by the number of workers that happened to read it.

    Args:
        units: The run's units

    Returns:
        Summed counters keyed by (unit name, symbol)
    """
    rows = [SignalStatsOfUnit(
        unit_name=unit.name, symbol=stats.symbol or unit.symbol, worker_name=stats.worker_name,
        signal_kind=stats.signal_kind, fresh_ticks=stats.fresh_ticks,
        stale_ticks=stats.stale_ticks, blind_ticks=stats.blind_ticks,
        availability_clamps=stats.availability_clamps,
        max_clamp_correction_ms=stats.max_clamp_correction_ms)
        for unit in units for stats in unit.signal_statistics]
    # Summed per worker, the clamps by MAX — declared in the signal resolution table
    index: Dict[Tuple[str, str], SignalResolutionStats] = {}
    for figures in fold_rows(rows, SIGNAL_RESOLUTION_TABLE):
        key = (figures['unit_name'], figures['symbol'])
        index[key] = SignalResolutionStats(**served_part(figures, SIGNAL_RESOLUTION_TABLE))
    return index


def _to_source_row(
    source: str,
    infos: List[SignalScenarioInfo],
    stats_index: Dict[Tuple[str, str], SignalResolutionStats],
) -> SignalSourceRow:
    """
    Map one signal source (its per-symbol coverages) to a renderable row.

    Args:
        source: The signal source identity (data_sentiment_type)
        infos: The source's per-symbol coverage entries
        stats_index: The run's counters, keyed by (unit name, symbol)

    Returns:
        SignalSourceRow with the archive facts + one usage row per scenario
    """
    # The provenance scalars are envelope-level, so they agree across a source's symbols;
    # the first coverage carrying values is representative.
    coverages = [info.coverage for info in infos]
    usages: List[SignalUsageRow] = []
    for info in infos:
        for usage in info.usages:
            usages.append(_to_usage_row(info.coverage, usage, stats_index))

    archive = served_part(fold_group(coverages, SIGNAL_COVERAGE_TABLE), SIGNAL_COVERAGE_TABLE)

    return SignalSourceRow(
        source=source,
        series_kind=SignalSeriesKind.ARCHIVE.value,
        **archive,
        usages=sorted(usages, key=lambda u: u.scenario),
    )


def _to_feed_row(
    observed: SignalObservedSeries,
    units: List[RunUnit],
) -> SignalSourceRow:
    """
    Map a live feed to a renderable row: what it stated about itself + what was decided on.

    The archive fields stay empty on purpose. `gap_counts` in particular is NOT filled with
    zeros: an empty map means "not measured", and a renderer that printed it as "no gaps"
    would assert continuity for a series that was never analysable. The live outage plane
    is the disturbance-episode protocol, which has its own section.

    Args:
        observed: What the transport accumulated while the session ran
        units: The run's units — an AutoTrader session has exactly one

    Returns:
        SignalSourceRow marked as feed-backed
    """
    stats_index = _index_resolution_stats(units)
    usages = [
        _to_feed_usage_row(observed, unit, stats_index)
        for unit in units
    ]
    return SignalSourceRow(
        source=observed.source,
        series_kind=SignalSeriesKind.FEED.value,
        sequence=observed.get_sequence_description(),
        data_origin=fold_group(observed.data_origins, FEED_PROVENANCE_TABLE)['value'],
        config_fingerprint=fold_group(observed.config_fingerprints, FEED_PROVENANCE_TABLE)['value'],
        prompt_version=fold_group(observed.prompt_versions, FEED_PROVENANCE_TABLE)['value'],
        cadence_seconds=observed.cadence_seconds,
        snapshot_count=observed.snapshot_count,
        archive_start=format_iso_or_empty(observed.start_time),
        archive_end=format_iso_or_empty(observed.end_time),
        gap_counts={},
        trigger_reasons=dict(observed.trigger_reasons),
        trigger_unknown=observed.trigger_unknown,
        usages=usages,
    )


def _to_feed_usage_row(
    observed: SignalObservedSeries,
    unit: RunUnit,
    stats_index: Dict[Tuple[str, str], SignalResolutionStats],
) -> SignalUsageRow:
    """
    Map a live-adapter session's consumption of a feed: its span + the decision-basis counters.

    `coverage_ratio` stays None — there is no archive window to have covered.

    Args:
        observed: The accumulated feed facts
        unit: The session's run unit
        stats_index: The run's counters, keyed by (unit name, symbol)

    Returns:
        SignalUsageRow without a coverage claim
    """
    symbol = observed.symbol or unit.symbol
    stats = stats_index.get((unit.name, symbol))
    clamps = stats.availability_clamps if stats else 0
    worst_clamp_ms = stats.max_clamp_correction_ms if stats else 0.0
    fresh = stats.fresh_ticks if stats else 0
    stale = stats.stale_ticks if stats else 0
    blind = stats.blind_ticks if stats else 0
    total = fresh + stale + blind

    return SignalUsageRow(
        scenario=unit.name,
        symbol=symbol,
        window_start=format_iso_or_empty(observed.start_time),
        window_end=format_iso_or_empty(observed.end_time),
        coverage_ratio=None,
        fresh_ticks=fresh,
        stale_ticks=stale,
        blind_ticks=blind,
        fresh_ratio=(fresh / total) if total else 0.0,
        availability_clamps=clamps,
        max_clamp_correction_ms=worst_clamp_ms,
    )


def _to_usage_row(
    coverage: SignalCoverageReport,
    usage: SignalScenarioUsage,
    stats_index: Dict[Tuple[str, str], SignalResolutionStats],
) -> SignalUsageRow:
    """
    Map one scenario's use of a source to a row: window coverage + resolution counters.

    Args:
        coverage: The source/symbol coverage report
        usage: The scenario's window
        stats_index: The run's counters, keyed by (unit name, symbol)

    Returns:
        SignalUsageRow (counters stay zero when the scenario produced no run unit)
    """
    stats = stats_index.get((usage.scenario_name, usage.symbol))
    clamps = stats.availability_clamps if stats else 0
    worst_clamp_ms = stats.max_clamp_correction_ms if stats else 0.0
    fresh = stats.fresh_ticks if stats else 0
    stale = stats.stale_ticks if stats else 0
    blind = stats.blind_ticks if stats else 0
    total = fresh + stale + blind

    # No window end and no archive end means there is nothing to measure a ratio against.
    # Left as None rather than defaulted to 1.0: a default of "fully covered" would assert
    # coverage for a window that could not be examined — the same class of false positive
    # that made an unanalysed feed render as "no gaps".
    window_end = usage.window_end or coverage.end_time
    coverage_ratio = (
        coverage.coverage_ratio_in_window(usage.window_start, window_end)
        if window_end is not None else None
    )

    return SignalUsageRow(
        scenario=usage.scenario_name,
        symbol=usage.symbol,
        window_start=format_iso_or_empty(usage.window_start),
        window_end=format_iso_or_empty(usage.window_end),
        coverage_ratio=coverage_ratio,
        fresh_ticks=fresh,
        stale_ticks=stale,
        blind_ticks=blind,
        fresh_ratio=(fresh / total) if total else 0.0,
        availability_clamps=clamps,
        max_clamp_correction_ms=worst_clamp_ms,
    )
