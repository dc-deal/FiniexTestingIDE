"""
Profiling report builder (#399) — the per-unit tick-loop profiling postprocessor.

Maps each scenario's raw profiling payload to a `ProfilingUnitRow` (operation timing + inter-tick
distribution + clipping), rolls them into the run-level `ProfilingAggregate`, and carries the
run-level warmup phases. **Sim-only** (the live tick loop has no operation profiling) — so, like
`scenario_details`, it reads the batch directly, NOT via `RunUnit`.

The raw `tick_loop_results.profiling_data` carries `profile_times` / `profile_counts` /
`inter_tick_intervals_ms`; the computed `ProfilingData` (operations + interval stats) is built from
them via the now non-mutating `ProfilingData.from_dicts` (#398) — same path the console renderer uses.
"""

from typing import Dict, Optional

from python.framework.reporting.run_reports.report_aggregators import aggregate_profiling
from python.framework.types.api.report_types import (
    ClippingRow, InterTickStatsRow, ProfilingOperationRow, ProfilingReport, ProfilingUnitRow,
    WarmupPhaseRow)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ClippingStats, ProcessResult
from python.framework.types.scenario_types.scenario_set_performance_types import ProfilingData
from python.framework.types.scenario_types.scenario_set_types import SingleScenario


def build_profiling_report_from_batch(batch: BatchExecutionSummary) -> ProfilingReport:
    """
    Build the profiling report from a sim batch — one unit per scenario with profiling data.

    Args:
        batch: The completed batch summary

    Returns:
        ProfilingReport with the per-unit rows, the run-level aggregate, and the warmup phases
    """
    clipping_map = batch.clipping_stats_map or {}
    rows = []
    for result in batch.process_result_list:
        tick_loop = getattr(result, 'tick_loop_results', None)
        if tick_loop is None:
            continue
        raw = tick_loop.profiling_data
        # Layer B (tick-loop profiling) off → no operation timing; skip this scenario.
        if raw is None or not raw.profile_times:
            continue
        scenario = batch.get_scenario_by_process_result(result)
        rows.append(_to_unit_row(result, raw, tick_loop, scenario, clipping_map))

    warmup = [
        WarmupPhaseRow(name=phase.name, duration_s=phase.duration_s)
        for phase in (batch.warmup_phases or [])
    ]
    return ProfilingReport(
        units=rows,
        aggregate=aggregate_profiling(rows, budget_active=bool(clipping_map)),
        warmup_phases=warmup)


def _to_unit_row(
    result: ProcessResult,
    raw,
    tick_loop,
    scenario: SingleScenario,
    clipping_map: Dict[int, ClippingStats],
) -> ProfilingUnitRow:
    """Map one scenario's raw profiling payload to a renderable row."""
    # Build the computed profiling (operations + interval stats) — the renderer's path.
    profiling = ProfilingData.from_dicts(
        raw.profile_times, raw.profile_counts,
        inter_tick_intervals_ms=raw.inter_tick_intervals_ms,
        gap_threshold_s=raw.gap_threshold_s, ticks_total=raw.ticks_total)

    ticks = tick_loop.coordination_statistics.ticks_processed
    total_ms = profiling.total_per_tick_ms

    operations = [
        ProfilingOperationRow(
            operation=name, total_time_ms=timing.total_time_ms, avg_time_ms=timing.avg_time_ms,
            call_count=timing.call_count,
            pct=(timing.total_time_ms / total_ms * 100) if total_ms > 0 else 0.0)
        for name, timing in profiling.operations.items()
    ]
    operations.sort(key=lambda op: op.pct, reverse=True)
    bottleneck_operation = operations[0].operation if operations else ''
    bottleneck_pct = operations[0].pct if operations else 0.0

    inter_tick = None
    if profiling.interval_stats:
        s = profiling.interval_stats
        inter_tick = InterTickStatsRow(
            min_ms=s.min_ms, p5_ms=s.p5_ms, median_ms=s.median_ms, mean_ms=s.mean_ms,
            p95_ms=s.p95_ms, max_ms=s.max_ms, interval_count=s.filtered_intervals,
            gaps_removed=s.gaps_removed, threshold_s=s.gap_threshold_s)

    clipping = _clipping_row(clipping_map.get(result.scenario_index))

    return ProfilingUnitRow(
        name=result.scenario_name, symbol=scenario.symbol,
        total_ticks=ticks, avg_per_tick_ms=(total_ms / ticks) if ticks > 0 else 0.0,
        total_ms=total_ms, bottleneck_operation=bottleneck_operation, bottleneck_pct=bottleneck_pct,
        operations=operations, inter_tick=inter_tick, clipping=clipping)


def _clipping_row(stats: Optional[ClippingStats]) -> Optional[ClippingRow]:
    """Map a scenario's ClippingStats to a row, or None when no budget was active."""
    if stats is None:
        return None
    return ClippingRow(
        ticks_total=stats.ticks_total, ticks_kept=stats.ticks_kept,
        ticks_clipped=stats.ticks_clipped, clipping_rate_pct=stats.clipping_rate_pct,
        budget_ms=stats.budget_ms)
