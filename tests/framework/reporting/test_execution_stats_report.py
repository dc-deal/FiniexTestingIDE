"""
Execution-Stats Report Builder Tests (#391).

Counts-only segment: order counts + SL/TP triggers, currency-agnostic. Tested against
a real BatchExecutionSummary / ProcessResult / ProcessTickLoopResult / SingleScenario
(not stand-ins), extracted into RunUnits — so it exercises the actual persist-path
attribute access (the symbol comes from the index-synced SingleScenario, since
ProcessResult carries none). The live builder uses a real AutoTraderResult.
"""

from datetime import datetime, timezone

from python.framework.reporting.run_reports.execution_stats_report_builder import build_execution_stats_report
from python.framework.reporting.run_reports.run_unit import (
    run_units_from_batch, run_units_from_session)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats


_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)


def _stats(sent: int = 5, executed: int = 4, rejected: int = 1, sl_tp: int = 2) -> ExecutionStats:
    """A real counts-only ExecutionStats fixture."""
    return ExecutionStats(
        orders_sent=sent, orders_executed=executed,
        orders_rejected=rejected, sl_tp_triggered=sl_tp)


def _process_result(name: str, idx: int, stats: ExecutionStats) -> ProcessResult:
    return ProcessResult(
        success=True, scenario_name=name, scenario_index=idx,
        tick_loop_results=ProcessTickLoopResult(execution_stats=stats))


def _scenario(name: str, idx: int, symbol: str) -> SingleScenario:
    return SingleScenario(
        name=name, scenario_index=idx, symbol=symbol,
        data_broker_type='mt5', start_date=_DT)


def _batch(extra_results=None) -> BatchExecutionSummary:
    """A real two-scenario batch with per-scenario execution stats."""
    results = [
        _process_result('s1', 0, _stats(sent=5, executed=4, rejected=1, sl_tp=2)),
        _process_result('s2', 1, _stats(sent=3, executed=3, rejected=0, sl_tp=1)),
    ]
    if extra_results:
        results.extend(extra_results)
    scenarios = [_scenario('s1', 0, 'EURUSD'), _scenario('s2', 1, 'GBPUSD')]
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=scenarios)


class TestBatch:
    """sim: N scenario units (symbol from SingleScenario) + summed totals."""

    def test_units_use_scenario_symbol(self):
        report = build_execution_stats_report(run_units_from_batch(_batch()))
        assert [u.name for u in report.units] == ['s1', 's2']
        # symbol is NOT on ProcessResult — must resolve via the index-synced scenario
        assert [u.symbol for u in report.units] == ['EURUSD', 'GBPUSD']

    def test_unit_counts_mapped(self):
        report = build_execution_stats_report(run_units_from_batch(_batch()))
        row = report.units[0]
        assert (row.orders_sent, row.orders_executed, row.orders_rejected, row.sl_tp_triggered) \
            == (5, 4, 1, 2)

    def test_totals_sum_currency_agnostic(self):
        report = build_execution_stats_report(run_units_from_batch(_batch()))
        assert report.totals.orders_sent == 8
        assert report.totals.orders_executed == 7
        assert report.totals.orders_rejected == 1
        assert report.totals.sl_tp_triggered == 3

    def test_skips_scenarios_without_stats(self):
        bad = ProcessResult(
            success=False, scenario_name='bad', scenario_index=2, tick_loop_results=None)
        report = build_execution_stats_report(run_units_from_batch(_batch(extra_results=[bad])))
        assert [u.name for u in report.units] == ['s1', 's2']


class TestSession:
    """live: 1 unit = its own totals."""

    def test_single_unit_and_totals(self):
        result = AutoTraderResult(
            execution_stats=_stats(sent=7, executed=6, rejected=1, sl_tp=4))
        report = build_execution_stats_report(
            run_units_from_session(result, 'my_profile', 'BTCUSD'))
        assert len(report.units) == 1
        assert report.units[0].name == 'my_profile'
        assert report.units[0].symbol == 'BTCUSD'
        assert report.totals.orders_executed == 6
        assert report.totals.sl_tp_triggered == 4

    def test_empty_when_no_stats(self):
        report = build_execution_stats_report(
            run_units_from_session(AutoTraderResult(execution_stats=None), 'p', 'BTCUSD'))
        assert report.units == []
        assert report.totals.orders_sent == 0
