"""
Worker/Decision Report Builder Tests (#398).

`build_worker_decision_report` maps RunUnits → `WorkerDecisionReport`: per-unit worker timing +
decision stats + coordination, plus the per-worker timing totals summed across units. Tested with
hand-built RunUnit fixtures (real `WorkerPerformanceStats` / `DecisionLogicStats`), including a
live-style unit without coordination and the empty case.
"""

from python.framework.reporting.run_reports.run_unit import RunUnit
from python.framework.reporting.run_reports.worker_decision_report_builder import (
    build_worker_decision_report)
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats, WorkerCoordinatorPerformanceStats, WorkerPerformanceStats)


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
                decision_total_time_ms=20.0, decision_avg_time_ms=0.02),
            coordination=WorkerCoordinatorPerformanceStats(
                parallel_workers=True, ticks_processed=1000, parallel_time_saved_ms=5.0))
        row = build_worker_decision_report([u]).units[0]
        assert row.name == 's1' and row.symbol == 'EURUSD'
        assert row.decision_logic_name == 'aggressive_trend'
        assert (row.buy_signals, row.sell_signals, row.flat_signals) == (5, 3, 92)
        assert row.decision_total_time_ms == 20.0
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
