"""
Worker/Decision Report Builder Tests (#398).

`build_worker_decision_report` maps RunUnits → `WorkerDecisionReport`: per-unit worker timing +
decision stats + coordination, plus the per-worker timing totals summed across units. Tested with
hand-built RunUnit fixtures (real `WorkerPerformanceStats` / `DecisionLogicStats`), including a
live-style unit without coordination and the empty case.
"""

import io
import re
from contextlib import redirect_stdout

from python.framework.reporting.console.performance_summary import PerformanceSummary
from python.framework.reporting.run_reports.run_unit import RunUnit
from python.framework.reporting.run_reports.worker_decision_report_builder import (
    build_worker_decision_report)
from python.framework.types.api.report_types import (
    WorkerDecisionReport, WorkerDecisionUnitRow, WorkerStatRow)
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats, WorkerCoordinatorPerformanceStats, WorkerPerformanceStats)
from python.framework.utils.console_renderer import ConsoleRenderer


def _ws(name, total, calls=1000) -> WorkerPerformanceStats:
    return WorkerPerformanceStats(
        worker_type='CORE/' + name, worker_name=name, worker_call_count=calls,
        worker_total_time_ms=total, worker_avg_time_ms=total / calls,
        worker_min_time_ms=0.1, worker_max_time_ms=total / calls * 2)


def _unit(name='s1', symbol='EURUSD', workers=None, decision=None, coordination=None) -> RunUnit:
    return RunUnit(
        name=name, symbol=symbol,
        worker_statistics=workers or [],
        decision_statistics=decision,
        coordination_statistics=coordination)


class TestBuild:
    def test_maps_unit_row(self):
        u = _unit(
            workers=[_ws('bollinger', 100.0), _ws('rsi', 80.0)],
            decision=DecisionLogicStats(
                decision_logic_type='CORE/aggressive_trend', decision_logic_name='aggressive_trend',
                buy_signals=5, sell_signals=3, flat_signals=92, trades_requested=2,
                decision_total_time_ms=20.0, decision_avg_time_ms=0.02,
                decision_min_time_ms=0.01, decision_max_time_ms=0.5),
            coordination=WorkerCoordinatorPerformanceStats(
                parallel_workers=True, ticks_processed=1000, parallel_time_saved_ms=5.0))
        row = build_worker_decision_report([u]).units[0]
        assert row.name == 's1' and row.symbol == 'EURUSD'
        assert row.decision_logic_name == 'aggressive_trend'
        assert (row.buy_signals, row.sell_signals, row.flat_signals) == (5, 3, 92)
        assert row.decision_total_time_ms == 20.0
        assert row.decision_min_time_ms == 0.01 and row.decision_max_time_ms == 0.5
        assert row.ticks_processed == 1000 and row.parallel_workers is True
        assert [w.worker_name for w in row.workers] == ['bollinger', 'rsi']

    def test_worker_totals_summed_across_units(self):
        u1 = _unit('s1', workers=[_ws('bollinger', 100.0, 1000), _ws('rsi', 80.0, 1000)])
        u2 = _unit('s2', workers=[_ws('bollinger', 60.0, 500), _ws('rsi', 40.0, 500)])
        totals = {w.worker_name: w for w in build_worker_decision_report([u1, u2]).worker_totals}
        assert totals['bollinger'].total_time_ms == 160.0           # 100 + 60
        assert totals['bollinger'].call_count == 1500               # 1000 + 500
        assert round(totals['bollinger'].avg_time_ms, 6) == round(160.0 / 1500, 6)  # sum/sum, not averaged

    def test_worker_totals_sorted_desc(self):
        u = _unit(workers=[_ws('rsi', 40.0), _ws('bollinger', 100.0)])
        totals = build_worker_decision_report([u]).worker_totals
        assert [w.worker_name for w in totals] == ['bollinger', 'rsi']   # by total_time_ms desc

    def test_live_unit_without_coordination(self):
        # live-style: no coordination_statistics → coordination fields stay at defaults
        u = _unit('LIVE', symbol='BTCUSD', workers=[_ws('bollinger', 10.0)],
                  decision=DecisionLogicStats(decision_total_time_ms=2.0))
        row = build_worker_decision_report([u]).units[0]
        assert row.ticks_processed == 0 and row.parallel_workers is False
        assert row.decision_total_time_ms == 2.0

    def test_unit_without_decision_stats(self):
        # decision_statistics None → decision fields at defaults
        row = build_worker_decision_report([_unit(workers=[_ws('bollinger', 5.0)], decision=None)]).units[0]
        assert row.decision_total_time_ms == 0.0 and row.decision_logic_name == ''

    def test_empty(self):
        report = build_worker_decision_report([])
        assert report.units == [] and report.worker_totals == []


class TestPerformanceRender:
    """PerformanceSummary (#399 3d) — the single model-fed worker/decision performance view."""

    def _report(self) -> WorkerDecisionReport:
        unit = WorkerDecisionUnitRow(
            name='GBPUSD_w01', symbol='GBPUSD', decision_logic_name='aggressive_trend',
            decision_logic_type='CORE/aggressive_trend', decision_count=559,
            decision_total_time_ms=269.0, decision_avg_time_ms=0.018,
            decision_min_time_ms=0.005, decision_max_time_ms=0.9,
            ticks_processed=15000, parallel_workers=False,
            workers=[
                WorkerStatRow(worker_type='CORE/bollinger', worker_name='bollinger_main',
                              call_count=15000, total_time_ms=1118.0, avg_time_ms=0.075,
                              min_time_ms=0.01, max_time_ms=0.5),
                WorkerStatRow(worker_type='CORE/rsi', worker_name='rsi_fast',
                              call_count=15000, total_time_ms=858.0, avg_time_ms=0.057,
                              min_time_ms=0.01, max_time_ms=0.4)])
        return WorkerDecisionReport(units=[unit])

    def _render(self, method_name: str) -> str:
        summary = PerformanceSummary(self._report())
        buf = io.StringIO()
        with redirect_stdout(buf):
            getattr(summary, method_name)(ConsoleRenderer())
        return re.sub(r'\x1b\[[0-9;]*m', '', buf.getvalue())

    def test_per_scenario_from_model(self):
        out = self._render('render_per_scenario')
        assert 'SCENARIO PERFORMANCE: GBPUSD_w01' in out
        assert 'WORKER DETAILS' in out and 'bollinger_main' in out
        assert 'DECISION LOGIC' in out and 'aggressive_trend' in out
        assert 'Range:  0.005- 0.900ms' in out      # decision min/max from the model

    def test_aggregated_from_model(self):
        out = self._render('render_aggregated')
        assert 'AGGREGATED SUMMARY' in out and 'WORKERS (AGGREGATED)' in out

    def test_bottleneck_from_model(self):
        out = self._render('render_bottleneck_analysis')
        assert 'BOTTLENECK ANALYSIS' in out and 'SLOWEST WORKER' in out

    def test_layer_a_off_suppressed(self):
        # no workers anywhere → section suppressed
        summary = PerformanceSummary(WorkerDecisionReport(units=[
            WorkerDecisionUnitRow(name='s1', symbol='EURUSD', workers=[])]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            summary.render_per_scenario(ConsoleRenderer())
        assert buf.getvalue() == ''
