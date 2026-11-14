"""
Worker/Decision Report Builder Tests (#398).

`build_worker_decision_report` maps RunUnits → `WorkerDecisionReport`: per-unit worker timing +
decision stats + coordination, plus the per-worker timing totals summed across units. Tested with
hand-built RunUnit fixtures (real `WorkerPerformanceStats` / `DecisionLogicStats`), including the
no-coordination fallback and the empty case.

**A unit without coordination used to be described here as "live-style", and that was the defect
wearing the words of a design decision.** An AutoTrader session's orchestrator counts its ticks exactly
as the simulation's does; what was missing until 2026-09-24 was the collection, so every session
reported 0 ticks beside its real decision count and the compute ratio derived from it read 0.0 %
rather than reading as absent. The fallback below is still real — a unit built from something
other than a run carries no coordination — it is simply not what live looks like.
"""

import io
import re
from contextlib import redirect_stdout

import pytest

from python.framework.reporting.builders.run_unit import RunUnit, run_units_from_session
from python.framework.reporting.builders.worker_decision_breakdown_builder import (
    build_worker_decision_breakdowns,
)
from python.framework.reporting.builders.worker_decision_report_builder import (
    build_worker_decision_report,
)
from python.framework.reporting.console.performance_summary import PerformanceSummary
from python.framework.types.api.report_types import (
    ProfilingOperationRow,
    ProfilingReport,
    ProfilingUnitRow,
    WorkerDecisionReport,
    WorkerDecisionUnitRow,
)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats,
    WorkerCoordinatorPerformanceStats,
    WorkerPerformanceStats,
)
from python.framework.utils.console_renderer import ConsoleRenderer

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'


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
                decision_min_time_ms=0.01, decision_max_time_ms=0.5, tracked=True),
            coordination=WorkerCoordinatorPerformanceStats(
                parallel_workers=True, ticks_processed=1000, parallel_time_saved_ms=5.0))
        row = build_worker_decision_report(_RUN_ID, [u]).units[0]
        assert row.name == 's1' and row.symbol == 'EURUSD'
        assert row.decision_logic_name == 'aggressive_trend'
        assert row.worker_decision_tracked is True
        assert (row.buy_signals, row.sell_signals, row.flat_signals) == (5, 3, 92)
        assert row.decision_total_time_ms == 20.0
        assert row.decision_min_time_ms == 0.01 and row.decision_max_time_ms == 0.5
        assert row.ticks_processed == 1000 and row.parallel_workers is True
        assert [w.worker_name for w in row.workers] == ['bollinger', 'rsi']

    def test_worker_totals_summed_across_units(self):
        u1 = _unit('s1', workers=[_ws('bollinger', 100.0, 1000), _ws('rsi', 80.0, 1000)])
        u2 = _unit('s2', workers=[_ws('bollinger', 60.0, 500), _ws('rsi', 40.0, 500)])
        totals = {w.worker_name: w for w in build_worker_decision_report(_RUN_ID, [u1, u2]).worker_totals}
        assert totals['bollinger'].total_time_ms == 160.0           # 100 + 60
        assert totals['bollinger'].call_count == 1500               # 1000 + 500
        assert round(totals['bollinger'].avg_time_ms, 6) == round(160.0 / 1500, 6)  # sum/sum, not averaged

    def test_worker_totals_sorted_desc(self):
        u = _unit(workers=[_ws('rsi', 40.0), _ws('bollinger', 100.0)])
        totals = build_worker_decision_report(_RUN_ID, [u]).worker_totals
        assert [w.worker_name for w in totals] == ['bollinger', 'rsi']   # by total_time_ms desc

    def test_a_unit_without_coordination_falls_back_to_defaults(self):
        """
        The fallback, not the live shape — see the module docstring.

        It still has to hold: a RunUnit can be built from something that is not a run at all
        (the booking-period builder makes one out of a trade list), and the row must render
        rather than divide by a tick count it does not have.
        """
        u = _unit('no-run', symbol='BTCUSD', workers=[_ws('bollinger', 10.0)],
                  decision=DecisionLogicStats(decision_total_time_ms=2.0, tracked=True))
        row = build_worker_decision_report(_RUN_ID, [u]).units[0]
        assert row.ticks_processed == 0 and row.parallel_workers is False
        assert row.decision_total_time_ms == 2.0
        # No tick processed: a ratio over zero ticks and an idle distance are not defined.
        assert row.workers[0].compute_ratio_pct is None
        assert row.workers[0].ticks_idle is None

    def test_a_live_session_unit_carries_its_tick_count(self):
        """
        The regression guard for the gap this file used to describe as intended.

        Built through `run_units_from_session`, the real live path, rather than through the
        hand-made `_unit` above — the defect was not in the mapping but in what the session
        handed it, so a fixture that sets the field by hand could not have caught it.
        """
        worker = _ws('bollinger', 10.0, calls=750)
        worker.worker_last_compute_tick = 2990
        session = AutoTraderResult(
            worker_statistics=[worker],
            decision_statistics=DecisionLogicStats(decision_total_time_ms=2.0, tracked=True),
            coordination_statistics=WorkerCoordinatorPerformanceStats(
                parallel_workers=False, ticks_processed=3000, parallel_time_saved_ms=0.0))

        units = run_units_from_session(session, name='btcusd_live', symbol='BTCUSD')
        row = build_worker_decision_report(_RUN_ID, units).units[0]

        assert row.ticks_processed == 3000
        # The two figures the tick count exists for (#420): how often the worker actually
        # computed against the ticks that arrived, and how far it has been idle since.
        assert row.workers[0].compute_ratio_pct == pytest.approx(25.0)
        assert row.workers[0].ticks_idle == 10

    def test_unit_without_decision_stats(self):
        # decision_statistics None → nothing is known about the logic, not a zero
        row = build_worker_decision_report(_RUN_ID, [_unit(workers=[_ws('bollinger', 5.0)], decision=None)]).units[0]
        assert row.decision_total_time_ms is None and row.decision_logic_name is None
        assert row.worker_decision_tracked is False

    def test_uncounted_decisions_are_null_not_zero(self):
        # The simulation's default: no tracker, so the logic decided on every tick and counted
        # none of it. The row names the logic and says the counters are unknown.
        u = _unit(decision=DecisionLogicStats(
                      decision_logic_type='CORE/aggressive_trend',
                      decision_logic_name='aggressive_trend'),
                  coordination=WorkerCoordinatorPerformanceStats(ticks_processed=2737))
        row = build_worker_decision_report(_RUN_ID, [u]).units[0]
        assert row.worker_decision_tracked is False
        assert row.decision_logic_type == 'CORE/aggressive_trend'
        assert row.decision_count is None and row.buy_signals is None
        assert row.trades_requested is None and row.decision_avg_time_ms is None
        assert row.ticks_processed == 2737
        assert row.workers == []
        dumped = row.model_dump(mode='json')
        assert dumped['decision_count'] is None and dumped['decision_logic_name'] == 'aggressive_trend'

    def test_empty(self):
        report = build_worker_decision_report(_RUN_ID, [])
        assert report.units == [] and report.worker_totals == []


class TestPerformanceRender:
    """PerformanceSummary (#399 3d) — the single model-fed worker/decision performance view."""

    def _report(self) -> WorkerDecisionReport:
        # Built as a run builds it, so the folded totals and the slowest performers are there
        unit = _unit(
            name='GBPUSD_w01', symbol='GBPUSD',
            workers=[_ws('bollinger_main', 1118.0, 15000), _ws('rsi_fast', 858.0, 15000)],
            decision=DecisionLogicStats(
                decision_logic_type='CORE/aggressive_trend', decision_logic_name='aggressive_trend',
                decision_count=559, buy_signals=40, sell_signals=19, flat_signals=500,
                decision_total_time_ms=269.0, decision_avg_time_ms=0.018,
                decision_min_time_ms=0.005, decision_max_time_ms=0.9, tracked=True),
            coordination=WorkerCoordinatorPerformanceStats(
                parallel_workers=False, ticks_processed=15000))
        return build_worker_decision_report(_RUN_ID, [unit])

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

    def test_no_recommendation_is_made_here(self):
        """Whether a component is too slow is a verdict — PostRunValidator decides it."""
        out = self._render('render_bottleneck_analysis')
        assert 'RECOMMENDATIONS' not in out
        assert 'Optimize' not in out
        assert 'Consider disabling' not in out
        assert 'No major bottlenecks detected' not in out, \
            'the clean-run line belongs to the section that owns the verdicts'

    def test_the_measurements_stay(self):
        """Only the judgement left — the numbers it was based on are still shown."""
        out = self._render('render_bottleneck_analysis')
        assert 'SLOWEST SCENARIO' in out and 'GBPUSD_w01' in out
        assert 'bollinger_main' in out

    def test_layer_a_off_suppressed(self):
        # no workers anywhere → section suppressed
        summary = PerformanceSummary(WorkerDecisionReport(run_id=_RUN_ID, units=[
            WorkerDecisionUnitRow(name='s1', symbol='EURUSD', workers=[])]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            summary.render_per_scenario(ConsoleRenderer())
        assert buf.getvalue() == ''


class TestCadenceDerivedInBuilder:
    """#420 cadence telemetry is derived once, not recomputed by the renderer (#391)."""

    def test_compute_ratio_and_idle(self):
        worker = _ws('rsi', total=100.0, calls=250)
        worker.worker_last_compute_tick = 900
        row = build_worker_decision_report(_RUN_ID, [_unit(
            workers=[worker],
            coordination=WorkerCoordinatorPerformanceStats(
                parallel_workers=True, ticks_processed=1000,
                parallel_time_saved_ms=5.0))]).units[0]
        assert row.workers[0].compute_ratio_pct == pytest.approx(25.0)   # 250 / 1000
        assert row.workers[0].ticks_idle == 100                          # 1000 - 900
        assert row.parallel_avg_saved_per_tick_ms == pytest.approx(0.005)

    def test_no_ticks_leaves_the_cadence_undefined(self):
        """A unit with no coordination stats has no tick count — the cadence figures are null."""
        row = build_worker_decision_report(_RUN_ID, [_unit(workers=[_ws('rsi', 100.0)])]).units[0]
        assert row.workers[0].compute_ratio_pct is None
        assert row.workers[0].ticks_idle is None
        assert row.parallel_avg_saved_per_tick_ms == 0.0

    def test_a_worker_that_never_computed_has_no_idle_distance(self):
        """It read 0 — "just computed" — for a worker that never ran."""
        worker = _ws('rsi', total=0.0, calls=1)
        worker.worker_last_compute_tick = -1
        row = build_worker_decision_report(_RUN_ID, [_unit(
            workers=[worker],
            coordination=WorkerCoordinatorPerformanceStats(ticks_processed=1000))]).units[0]
        assert row.workers[0].ticks_idle is None
        assert row.workers[0].compute_ratio_pct == pytest.approx(0.1)


def _decided(name, total_ms, avg_ms, count=100, buy=10, sell=5, logic='trend') -> DecisionLogicStats:
    """A tracked decision logic's statistics."""
    return DecisionLogicStats(
        decision_logic_type='CORE/' + logic, decision_logic_name=logic, decision_count=count,
        buy_signals=buy, sell_signals=sell, flat_signals=count - buy - sell,
        decision_total_time_ms=total_ms, decision_avg_time_ms=avg_ms,
        decision_min_time_ms=0.0, decision_max_time_ms=avg_ms * 2, tracked=True)


def _ticks(count, parallel=False, saved=0.0) -> WorkerCoordinatorPerformanceStats:
    """A unit's coordination: its ticks, and whether its workers ran in parallel."""
    return WorkerCoordinatorPerformanceStats(
        parallel_workers=parallel, ticks_processed=count, parallel_time_saved_ms=saved)


class TestTheFiguresTheConsolePrintsAreFolded:
    """
    Every figure of the aggregated block and the bottleneck scan is on the report (#557): the
    units' own sums, the decision totals and the slowest performers, over the tracked units.
    """

    def test_a_unit_carries_its_workers_and_its_algo_time(self):
        row = build_worker_decision_report(_RUN_ID, [_unit(
            workers=[_ws('a', 30.0), _ws('b', 10.0)], decision=_decided('s1', 20.0, 0.2),
            coordination=_ticks(100))]).units[0]
        assert (row.timed_worker_count, row.worker_call_count, row.worker_time_ms) == (2, 2000, 40.0)
        assert (row.algo_time_ms, row.algo_avg_per_tick_ms) == (60.0, 0.6)
        assert row.non_flat_signals == 15

    def test_an_untracked_unit_has_no_algo_time(self):
        row = build_worker_decision_report(_RUN_ID, [_unit(workers=[], coordination=_ticks(100))]).units[0]
        assert (row.algo_time_ms, row.algo_avg_per_tick_ms, row.non_flat_signals) == (None, None, None)

    def test_the_decision_totals_over_the_tracked_units(self):
        report = build_worker_decision_report(_RUN_ID, [
            _unit('s1', decision=_decided('s1', 20.0, 0.2, count=100), coordination=_ticks(1000)),
            _unit('s2', decision=_decided('s2', 10.0, 0.4, count=25, buy=5, sell=0),
                  coordination=_ticks(500)),
            _unit('s3', coordination=_ticks(700))])          # untracked: not counted
        t = report.decision_totals
        assert (t.total_ticks, t.total_decisions, t.total_non_flat_signals) == (1500, 125, 20)
        assert t.decision_total_time_ms == 30.0
        assert t.decision_avg_time_ms == 30.0 / 125
        assert t.decision_unit_mean_time_ms == pytest.approx(0.3)

    def test_the_worker_totals_carry_the_mean_of_the_units_means(self):
        report = build_worker_decision_report(_RUN_ID, [
            _unit('s1', workers=[_ws('a', 10.0, 100)]), _unit('s2', workers=[_ws('a', 40.0, 100)])])
        total = report.worker_totals[0]
        assert total.unit_mean_time_ms == pytest.approx((0.1 + 0.4) / 2)
        assert report.units[0].workers[0].unit_mean_time_ms is None   # a unit's row has none

    def test_the_slowest_performers(self):
        report = build_worker_decision_report(_RUN_ID, [
            _unit('s1', workers=[_ws('a', 10.0, 100)], decision=_decided('s1', 5.0, 0.05),
                  coordination=_ticks(100, parallel=True, saved=-2.0)),
            _unit('s2', workers=[_ws('a', 40.0, 100)], decision=_decided('s2', 5.0, 0.05),
                  coordination=_ticks(100, parallel=True, saved=3.0))])
        b = report.bottlenecks
        assert (b.slowest_algo_unit, b.slowest_algo_unit_avg_per_tick_ms,
                b.slowest_algo_unit_time_ms) == ('s2', 0.45, 45.0)
        assert (b.slowest_worker, b.slowest_worker_worst_unit) == ('a', 's2')
        assert b.slowest_worker_worst_unit_ms == pytest.approx(0.4)
        assert (b.slowest_decision_logic, b.slowest_decision_logic_mean_ms) == ('trend', 0.05)
        assert (b.worst_parallel_unit, b.worst_parallel_time_saved_ms) == ('s1', -2.0)

    def test_the_first_among_equals_and_nothing_above_zero(self):
        equal = build_worker_decision_report(_RUN_ID, [
            _unit('s1', workers=[_ws('a', 10.0, 100)], decision=_decided('s1', 0.0, 0.0),
                  coordination=_ticks(100)),
            _unit('s2', workers=[_ws('a', 10.0, 100)], decision=_decided('s2', 0.0, 0.0),
                  coordination=_ticks(100))]).bottlenecks
        assert equal.slowest_algo_unit == 's1' and equal.slowest_worker_worst_unit == 's1'
        # No decision time above zero: no slowest logic; no parallel unit: no worst one
        assert equal.slowest_decision_logic is None and equal.worst_parallel_unit is None
        # Nothing tracked: no totals and no bottlenecks — never zeros that claim a measurement
        nothing = build_worker_decision_report(_RUN_ID, [_unit('s1', coordination=_ticks(100))])
        assert nothing.decision_totals is None and nothing.bottlenecks is None


class TestTheBreakdownJoinsBothLayers:
    """Where a unit's worker_decision time went (#399), derived on the profiling model (#557)."""

    def _profiling(self, name, total_ms):
        return ProfilingReport(run_id=_RUN_ID, units=[ProfilingUnitRow(
            name=name, symbol='EURUSD', total_ticks=100,
            operations=[ProfilingOperationRow(operation='worker_decision', total_time_ms=total_ms)])])

    def test_the_coordination_is_what_the_two_leave(self):
        report = build_worker_decision_report(_RUN_ID, [_unit(
            workers=[_ws('a', 50.0)], decision=_decided('s1', 30.0, 0.3), coordination=_ticks(100))])
        row = build_worker_decision_breakdowns(self._profiling('s1', 100.0), report)[0]
        assert (row.worker_execution_ms, row.decision_logic_ms, row.coordination_overhead_ms) == (
            50.0, 30.0, 20.0)
        assert (row.worker_execution_pct, row.decision_logic_pct,
                row.coordination_overhead_pct) == (50.0, 30.0, 20.0)

    def test_never_below_zero_and_only_where_both_measured(self):
        report = build_worker_decision_report(_RUN_ID, [_unit(
            workers=[_ws('a', 80.0)], decision=_decided('s1', 40.0, 0.4), coordination=_ticks(100))])
        assert build_worker_decision_breakdowns(
            self._profiling('s1', 100.0), report)[0].coordination_overhead_ms == 0.0
        assert build_worker_decision_breakdowns(self._profiling('other', 100.0), report) == []
        assert build_worker_decision_breakdowns(self._profiling('s1', 0.0), report) == []


class TestTheBottleneckScanShowsMeasurementsWithoutThresholds:
    """
    The scan prints what it measured (#557, 684.a): no fixed "too slow" threshold decides whether
    the slowest decision logic appears, and the worst parallel unit appears whether its workers
    saved time or cost it — that verdict is the post-run validator's.
    """

    def _render(self, report) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            PerformanceSummary(report).render_bottleneck_analysis(ConsoleRenderer())
        return re.sub(r'\x1b\[[0-9;]*m', '', buf.getvalue())

    def test_a_fast_decision_logic_and_a_parallel_gain_are_still_shown(self):
        report = build_worker_decision_report(_RUN_ID, [
            _unit('s1', workers=[_ws('a', 10.0, 100)], decision=_decided('s1', 1.0, 0.01),
                  coordination=_ticks(100, parallel=True, saved=3.0))])
        out = self._render(report)
        assert 'SLOWEST DECISION LOGIC' in out and '0.010ms' in out
        assert 'WORST PARALLEL EFFICIENCY' in out and 'Faster' in out
