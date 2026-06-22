"""
Scenario-Details Report Builder Tests (#391/#393).

Maps each scenario's ProcessResult (+ index-synced SingleScenario) to a row: status
(success / failed / hybrid), execution metadata, and the decision-logic signal counts.
Tested with real ProcessResult / ProcessTickLoopResult / SingleScenario fixtures — failed
scenarios (no tick_loop_results) must still appear.
"""

from datetime import datetime, timezone

from python.framework.reporting.builders.scenario_details_report_builder import (
    build_scenario_details_report_from_batch)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats, WorkerCoordinatorPerformanceStats, WorkerPerformanceStats)
from python.framework.types.process_data_types import (
    ProcessResult, ProcessTickLoopResult, TickRangeStats)
from python.framework.types.scenario_types.scenario_set_types import SingleScenario


_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)
_DT2 = datetime(2025, 10, 13, 1, 0, 0, tzinfo=timezone.utc)


def _ws() -> WorkerPerformanceStats:
    return WorkerPerformanceStats(
        worker_type='CORE/rsi', worker_name='rsi', worker_call_count=0,
        worker_total_time_ms=0.0, worker_avg_time_ms=0.0,
        worker_min_time_ms=0.0, worker_max_time_ms=0.0)


def _tick_loop(buy=296, sell=263, flat=14441, trades=2, ticks=15000) -> ProcessTickLoopResult:
    return ProcessTickLoopResult(
        decision_statistics=DecisionLogicStats(
            buy_signals=buy, sell_signals=sell, flat_signals=flat, trades_requested=trades),
        coordination_statistics=WorkerCoordinatorPerformanceStats(ticks_processed=ticks),
        tick_range_stats=TickRangeStats(
            first_tick_time=_DT, last_tick_time=_DT2, tick_timespan_seconds=3600.0),
        worker_statistics=[_ws(), _ws()])


def _result(name, idx, tick_loop=None, error_type='', error_message='') -> ProcessResult:
    return ProcessResult(
        success=not error_type, scenario_name=name, scenario_index=idx,
        tick_loop_results=tick_loop, error_type=error_type, error_message=error_message)


def _scenario(name, idx, symbol, account_currency='', explicit=False) -> SingleScenario:
    scenario = SingleScenario(
        name=name, scenario_index=idx, symbol=symbol, data_broker_type='mt5', start_date=_DT)
    scenario.account_currency = account_currency
    if explicit:
        scenario.trade_simulator_config = {'account_currency': account_currency}
    return scenario


def _batch(results, scenarios) -> BatchExecutionSummary:
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=scenarios)


class TestBuild:
    def test_success_row(self):
        batch = _batch([_result('s1', 0, tick_loop=_tick_loop())], [_scenario('s1', 0, 'EURUSD')])
        row = build_scenario_details_report_from_batch(batch).units[0]
        assert row.status == 'success'
        assert row.data_source == 'mt5' and row.symbol == 'EURUSD'
        assert (row.buy_signals, row.sell_signals, row.flat_signals) == (296, 263, 14441)
        assert row.trades_requested == 2 and row.ticks_processed == 15000
        assert row.worker_count == 2
        assert row.first_tick_time.endswith('+00:00')

    def test_failed_row_no_tick_loop(self):
        batch = _batch(
            [_result('bad', 0, error_type='ValidationError', error_message='start before data')],
            [_scenario('bad', 0, 'BTCUSD')])
        row = build_scenario_details_report_from_batch(batch).units[0]
        assert row.status == 'failed'
        assert row.error_type == 'ValidationError' and row.error_message == 'start before data'
        assert row.ticks_processed == 0 and row.worker_count == 0

    def test_hybrid_row(self):
        batch = _batch(
            [_result('s1', 0, tick_loop=_tick_loop(), error_type='RuntimeError', error_message='boom')],
            [_scenario('s1', 0, 'EURUSD')])
        row = build_scenario_details_report_from_batch(batch).units[0]
        assert row.status == 'hybrid'
        assert row.error_type == 'RuntimeError'
        assert row.ticks_processed == 15000        # partial data preserved

    def test_account_currency_explicit(self):
        # account_currency set in config → row carries it + the explicit marker
        batch = _batch(
            [_result('s1', 0, tick_loop=_tick_loop())],
            [_scenario('s1', 0, 'EURUSD', account_currency='USD', explicit=True)])
        row = build_scenario_details_report_from_batch(batch).units[0]
        assert row.account_currency == 'USD'
        assert row.account_currency_explicit is True

    def test_account_currency_derived(self):
        # derived (not in config) → currency present, explicit marker off
        batch = _batch(
            [_result('s1', 0, tick_loop=_tick_loop())],
            [_scenario('s1', 0, 'EURUSD', account_currency='USD', explicit=False)])
        row = build_scenario_details_report_from_batch(batch).units[0]
        assert row.account_currency == 'USD'
        assert row.account_currency_explicit is False
