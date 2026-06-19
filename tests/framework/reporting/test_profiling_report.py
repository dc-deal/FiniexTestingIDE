"""
Profiling Report Builder + Aggregator Tests (#399).

`build_profiling_report_from_batch` maps each scenario's raw `ProcessProfileData` to a
`ProfilingUnitRow` (operation timing + inter-tick + clipping) and rolls them up via
`aggregate_profiling`. Tested with **real** ProcessResult / ProcessTickLoopResult /
ProcessProfileData / BatchExecutionSummary fixtures (the reporting-test convention) — plus a
Layer-B-off scenario (no profiling) and the clipping path; the aggregator is exercised directly
on real `ProfilingUnitRow` rows.
"""

from datetime import datetime, timezone

from python.framework.reporting.run_reports.profiling_report_builder import (
    build_profiling_report_from_batch)
from python.framework.reporting.run_reports.report_aggregators import aggregate_profiling
from python.framework.types.api.report_types import (
    ClippingRow, InterTickStatsRow, ProfilingOperationRow, ProfilingUnitRow)
from python.framework.types.batch_execution_types import BatchExecutionSummary, WarmupPhaseEntry
from python.framework.types.performance_types.performance_stats_types import (
    WorkerCoordinatorPerformanceStats)
from python.framework.types.process_data_types import (
    ClippingStats, ProcessProfileData, ProcessResult, ProcessTickLoopResult)
from python.framework.types.scenario_types.scenario_set_types import SingleScenario


_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)


def _ppd(times, ticks, intervals=None) -> ProcessProfileData:
    """Raw profile payload: per-op total times (+ the special total_per_tick) and counts."""
    profile_times = dict(times)
    profile_times['total_per_tick'] = sum(times.values())
    return ProcessProfileData(
        profile_times=profile_times, profile_counts={op: ticks for op in times},
        inter_tick_intervals_ms=intervals, gap_threshold_s=300.0, ticks_total=0)


def _result(name, idx, times, ticks, intervals=None) -> ProcessResult:
    tick_loop = ProcessTickLoopResult(
        profiling_data=_ppd(times, ticks, intervals),
        coordination_statistics=WorkerCoordinatorPerformanceStats(ticks_processed=ticks))
    return ProcessResult(success=True, scenario_name=name, scenario_index=idx,
                         tick_loop_results=tick_loop)


def _scenario(name, idx, symbol='EURUSD') -> SingleScenario:
    return SingleScenario(
        name=name, scenario_index=idx, symbol=symbol, data_broker_type='mt5', start_date=_DT)


def _batch(results, scenarios, clipping=None, warmup=None) -> BatchExecutionSummary:
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=scenarios,
        clipping_stats_map=clipping, warmup_phases=warmup)


class TestBuild:
    def test_unit_row_operations(self):
        batch = _batch(
            [_result('s1', 0, {'live_update': 600.0, 'worker_decision': 300.0, 'bar_rendering': 100.0}, 5000)],
            [_scenario('s1', 0, 'EURUSD')])
        row = build_profiling_report_from_batch(batch).units[0]
        assert row.name == 's1' and row.symbol == 'EURUSD'
        assert row.total_ticks == 5000
        assert row.total_ms == 1000.0
        assert round(row.avg_per_tick_ms, 4) == 0.2            # 1000 / 5000
        # operations sorted by share, total_per_tick excluded
        assert [o.operation for o in row.operations] == ['live_update', 'worker_decision', 'bar_rendering']
        assert round(row.operations[0].pct, 1) == 60.0         # 600 / 1000
        assert round(row.operations[0].avg_time_ms, 3) == 0.12  # 600 / 5000
        assert row.bottleneck_operation == 'live_update' and round(row.bottleneck_pct, 1) == 60.0

    def test_inter_tick_mapped(self):
        batch = _batch(
            [_result('s1', 0, {'worker_decision': 100.0}, 100, intervals=[1.0, 2.0, 3.0, 4.0, 5.0])],
            [_scenario('s1', 0)])
        row = build_profiling_report_from_batch(batch).units[0]
        assert row.inter_tick is not None
        assert row.inter_tick.min_ms == 1.0 and row.inter_tick.max_ms == 5.0
        assert row.inter_tick.interval_count == 5

    def test_clipping_mapped(self):
        clipping = {0: ClippingStats(ticks_total=6000, ticks_kept=5000, ticks_clipped=1000,
                                     clipping_rate_pct=16.67, budget_ms=1.5)}
        batch = _batch(
            [_result('s1', 0, {'worker_decision': 100.0}, 5000)],
            [_scenario('s1', 0)], clipping=clipping)
        row = build_profiling_report_from_batch(batch).units[0]
        assert row.clipping is not None
        assert row.clipping.ticks_clipped == 1000 and row.clipping.budget_ms == 1.5

    def test_warmup_carried(self):
        batch = _batch(
            [_result('s1', 0, {'worker_decision': 100.0}, 100)],
            [_scenario('s1', 0)],
            warmup=[WarmupPhaseEntry(name='Data Loading', duration_s=9.47)])
        report = build_profiling_report_from_batch(batch)
        assert [(w.name, w.duration_s) for w in report.warmup_phases] == [('Data Loading', 9.47)]

    def test_layer_b_off_scenario_skipped(self):
        # profile_times empty → no operation timing → unit skipped
        tick_loop = ProcessTickLoopResult(
            profiling_data=ProcessProfileData(profile_times={}, profile_counts={}),
            coordination_statistics=WorkerCoordinatorPerformanceStats(ticks_processed=100))
        result = ProcessResult(success=True, scenario_name='off', scenario_index=0,
                               tick_loop_results=tick_loop)
        report = build_profiling_report_from_batch(_batch([result], [_scenario('off', 0)]))
        assert report.units == []


class TestAggregate:
    def _unit(self, name, ops, ticks, total_ms, bottleneck, p5):
        return ProfilingUnitRow(
            name=name, symbol='EURUSD', total_ticks=ticks, total_ms=total_ms,
            avg_per_tick_ms=total_ms / ticks, bottleneck_operation=bottleneck,
            operations=[ProfilingOperationRow(operation=o, avg_time_ms=a) for o, a in ops],
            inter_tick=InterTickStatsRow(p5_ms=p5))

    def test_aggregate_basics(self):
        u1 = self._unit('s1', [('live_update', 0.8), ('worker_decision', 0.3)], 1000, 1000.0, 'live_update', 1.0)
        u2 = self._unit('s2', [('live_update', 0.6), ('worker_decision', 0.4)], 500, 600.0, 'live_update', 2.0)
        agg = aggregate_profiling([u1, u2], budget_active=False)
        assert agg.scenarios == 2 and agg.total_ticks == 1500
        assert round(agg.avg_per_tick_ms, 4) == round(1600.0 / 1500, 4)    # total/ticks
        assert agg.most_common_bottleneck == 'live_update' and agg.most_common_bottleneck_pct == 100.0
        assert agg.p5_min_ms == 1.0 and agg.p5_max_ms == 2.0
        # cross-scenario avg op time = mean of per-unit avg
        avg = {o.operation: o.avg_time_ms for o in agg.avg_operation_times}
        assert round(avg['live_update'], 2) == 0.70 and round(avg['worker_decision'], 2) == 0.35

    def test_bottleneck_status_infra_vs_expected(self):
        # live_update (infra) bottleneck in 100% → critical; worker_decision (expected) never → none
        u = self._unit('s1', [('live_update', 0.8), ('worker_decision', 0.3)], 1000, 1000.0, 'live_update', 1.0)
        agg = aggregate_profiling([u], budget_active=False)
        status = {b.operation: b.status for b in agg.bottlenecks}
        assert status['live_update'] == 'critical'
        assert status['worker_decision'] == 'none'

    def test_expected_bottleneck_status(self):
        u = self._unit('s1', [('worker_decision', 0.3)], 1000, 1000.0, 'worker_decision', 1.0)
        agg = aggregate_profiling([u], budget_active=False)
        assert {b.operation: b.status for b in agg.bottlenecks}['worker_decision'] == 'expected'

    def test_budget_recommendation(self):
        u = self._unit('s1', [('worker_decision', 0.3)], 1000, 1200.0, 'worker_decision', 1.0)
        agg = aggregate_profiling([u], budget_active=False)
        assert agg.p95_processing_ms == 1.2                  # only avg/tick
        assert agg.suggested_budget_ms == round(1.2 * 1.1, 3)  # P95 + 10%

    def test_clipping_totals(self):
        u = ProfilingUnitRow(
            name='s1', symbol='EURUSD', total_ticks=5000, total_ms=1000.0, avg_per_tick_ms=0.2,
            bottleneck_operation='worker_decision',
            operations=[ProfilingOperationRow(operation='worker_decision', avg_time_ms=0.2)],
            clipping=ClippingRow(ticks_total=6000, ticks_kept=5000, ticks_clipped=1000,
                                 clipping_rate_pct=16.67, budget_ms=1.5))
        agg = aggregate_profiling([u], budget_active=True)
        assert agg.clipping_total_clipped == 1000 and agg.clipping_total_ticks == 6000
        assert agg.clipping_budgets == [1.5]

    def test_empty(self):
        agg = aggregate_profiling([], budget_active=False)
        assert agg.scenarios == 0 and agg.bottlenecks == [] and agg.avg_operation_times == []
