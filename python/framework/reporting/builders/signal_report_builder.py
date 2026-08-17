"""
Signal report builder (#433) — the signal-configuration postprocessor.

Joins the two planes a signal source has:
- ARCHIVE (what the source could offer): provenance, measured cadence and gaps, read from
  the SignalCoverageReport that the shared data preparation built in Phase 1 (#447).
- RUNTIME (what the strategy actually decided on): the per-tick fresh/stale/blind counters
  the SIGNAL workers captured (#433 Part C), carried on the RunUnits.

Unified across both pipelines: sim batch and AutoTrader-mock session run through the SAME
MountPreparer, so both hand in the same signal scenario map and one entry point serves both.
Reads the map directly — NOT via RunUnit — because it is a source-keyed data snapshot, not a
per-unit record (same pattern as the broker section).
"""

from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from python.framework.discoveries.signal_coverage.signal_coverage_report import (
    SignalCoverageReport)
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    SignalReport, SignalSourceRow, SignalUsageRow)
from python.framework.types.scenario_types.scenario_set_types import (
    SignalScenarioInfo, SignalScenarioUsage)
from python.framework.types.signal_data_types import SignalResolutionStats


def build_signal_report(
    signal_scenario_map: Dict[Tuple[str, str], SignalScenarioInfo],
    units: List[RunUnit],
) -> SignalReport:
    """
    Build the signal report from the prepared signal map + the run's units.

    Args:
        signal_scenario_map: (source, symbol) → coverage + the scenario windows bound to it
        units: The run's units (sim: N scenarios; live: 1 session) — carry the counters

    Returns:
        SignalReport with one unit per signal source (empty when no source is bound)
    """
    # Early exit: no scenario binds a signal source
    if not signal_scenario_map:
        return SignalReport(units=[])

    stats_index = _index_resolution_stats(units)

    # Group by source — one console block per source, its symbols folded into the usages.
    by_source: Dict[str, List[SignalScenarioInfo]] = {}
    for info in signal_scenario_map.values():
        by_source.setdefault(info.data_sentiment_type, []).append(info)

    rows = [
        _to_source_row(source, infos, stats_index)
        for source, infos in sorted(by_source.items())
    ]
    return SignalReport(units=rows)


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
    thresholds); their counters are summed, because the report's granularity is the
    scenario's use of a source, not the worker instance.

    Args:
        units: The run's units

    Returns:
        Summed counters keyed by (unit name, symbol)
    """
    index: Dict[Tuple[str, str], SignalResolutionStats] = {}
    for unit in units:
        for stats in unit.signal_statistics:
            key = (unit.name, stats.symbol or unit.symbol)
            merged = index.get(key)
            if merged is None:
                index[key] = SignalResolutionStats(
                    worker_name=stats.worker_name,
                    signal_kind=stats.signal_kind,
                    symbol=key[1],
                    fresh_ticks=stats.fresh_ticks,
                    stale_ticks=stats.stale_ticks,
                    blind_ticks=stats.blind_ticks,
                )
                continue
            merged.fresh_ticks += stats.fresh_ticks
            merged.stale_ticks += stats.stale_ticks
            merged.blind_ticks += stats.blind_ticks
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

    trigger_reasons, trigger_unknown = _merge_trigger_reasons(coverages)

    return SignalSourceRow(
        source=source,
        data_origin=_first_non_empty(c.get_data_origin() for c in coverages),
        config_fingerprint=_first_non_empty(
            c.get_config_fingerprint() for c in coverages),
        cadence_seconds=max((c.cadence_seconds for c in coverages), default=0.0),
        snapshot_count=max((c.snapshot_count for c in coverages), default=0),
        archive_start=_iso(min((c.start_time for c in coverages if c.start_time),
                               default=None)),
        archive_end=_iso(max((c.end_time for c in coverages if c.end_time), default=None)),
        gap_counts=_merge_gap_counts(coverages),
        trigger_reasons=trigger_reasons,
        trigger_unknown=trigger_unknown,
        usages=sorted(usages, key=lambda u: u.scenario),
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
    fresh = stats.fresh_ticks if stats else 0
    stale = stats.stale_ticks if stats else 0
    blind = stats.blind_ticks if stats else 0
    total = fresh + stale + blind

    window_end = usage.window_end or coverage.end_time
    coverage_ratio = 1.0
    if window_end is not None:
        coverage_ratio = coverage.coverage_ratio_in_window(usage.window_start, window_end)

    return SignalUsageRow(
        scenario=usage.scenario_name,
        symbol=usage.symbol,
        window_start=_iso(usage.window_start),
        window_end=_iso(usage.window_end),
        coverage_ratio=coverage_ratio,
        fresh_ticks=fresh,
        stale_ticks=stale,
        blind_ticks=blind,
        fresh_ratio=(fresh / total) if total else 0.0,
    )


def _merge_gap_counts(coverages: List[SignalCoverageReport]) -> Dict[str, int]:
    """Sum the gap counts across a source's symbols, dropping empty categories."""
    merged: Dict[str, int] = {}
    for coverage in coverages:
        for category, count in coverage.gap_counts.items():
            if count:
                merged[category] = merged.get(category, 0) + count
    return merged


def _merge_trigger_reasons(
        coverages: List[SignalCoverageReport]) -> Tuple[Dict[str, int], int]:
    """
    Trigger composition of a source — envelope-level, so taken from ONE coverage.

    Summing across symbols would multiply every envelope by its symbol count. The
    unknown count travels with the composition: both come from the same coverage,
    so the pair always describes the same envelope set.

    Args:
        coverages: The source's per-symbol coverage reports

    Returns:
        Tuple of (composition, count of envelopes carrying no reason)
    """
    for coverage in coverages:
        if coverage.trigger_reasons or coverage.trigger_unknown:
            return dict(coverage.trigger_reasons), coverage.trigger_unknown
    return {}, 0


def _first_non_empty(values: Iterable[str]) -> str:
    """First non-empty provenance value; '' when none carries one (= unknown)."""
    for value in values:
        if value:
            return value
    return ''


def _iso(moment: Optional[datetime]) -> str:
    """ISO-8601 string of a UTC moment, '' when absent."""
    return moment.isoformat() if moment is not None else ''
