"""
Shared Report Coordinator Tests (#403).

`SharedReportCoordinator.derive_and_persist` is the units-derived DERIVE+PERSIST core both
pipelines delegate to. Tested against a real BatchExecutionSummary / ProcessResult /
SingleScenario (sim) and a real AutoTraderResult (live) — not stand-ins — so it exercises the
actual write-path: all 7 sections' artifacts land in the io/ dir and the returned
UnifiedReports carries the same models the caller reuses for console + ledger.
"""

from datetime import datetime, timezone

from python.framework.reporting.builders.run_unit import (
    run_units_from_batch, run_units_from_session)
from python.framework.reporting.builders.unified_reports import UnifiedReports
from python.framework.reporting.shared_report_coordinator import SharedReportCoordinator
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats


_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)

# Every section writes these artifacts into the io/ dir (json for all 7, csv for three).
_EXPECTED_FILES = [
    'trade_history.json', 'trade_history.csv',
    'order_history.json', 'order_history.csv',
    'portfolio.json',
    'pending_orders.json',
    'execution_stats.json', 'execution_stats.csv',
    'run_summary.json',
    'worker_decision.json',
]


def _stats(sent=5, executed=4, rejected=1, sl_tp=2) -> ExecutionStats:
    return ExecutionStats(
        orders_sent=sent, orders_executed=executed,
        orders_rejected=rejected, sl_tp_triggered=sl_tp)


def _batch() -> BatchExecutionSummary:
    """A real two-scenario batch with per-scenario execution stats."""
    results = [
        ProcessResult(success=True, scenario_name='s1', scenario_index=0,
                      tick_loop_results=ProcessTickLoopResult(execution_stats=_stats(5, 4, 1, 2))),
        ProcessResult(success=True, scenario_name='s2', scenario_index=1,
                      tick_loop_results=ProcessTickLoopResult(execution_stats=_stats(3, 3, 0, 1))),
    ]
    scenarios = [
        SingleScenario(name='s1', scenario_index=0, symbol='EURUSD', data_broker_type='mt5', start_date=_DT),
        SingleScenario(name='s2', scenario_index=1, symbol='GBPUSD', data_broker_type='mt5', start_date=_DT),
    ]
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=scenarios)


class TestDeriveAndPersist:
    """The shared core: writes the 7 sections + returns the populated DTO."""

    def test_writes_all_artifacts_batch(self, tmp_path):
        io_dir = tmp_path / 'io'
        SharedReportCoordinator.derive_and_persist(run_units_from_batch(_batch()), io_dir)
        for name in _EXPECTED_FILES:
            assert (io_dir / name).exists(), f'missing artifact: {name}'

    def test_creates_io_dir_if_missing(self, tmp_path):
        # A nested, not-yet-existing io/ path must be created by the coordinator.
        io_dir = tmp_path / 'run' / 'io'
        SharedReportCoordinator.derive_and_persist(run_units_from_batch(_batch()), io_dir)
        assert io_dir.is_dir()
        assert (io_dir / 'run_summary.json').exists()

    def test_returns_populated_unified_reports(self, tmp_path):
        unified = SharedReportCoordinator.derive_and_persist(
            run_units_from_batch(_batch()), tmp_path / 'io')
        assert isinstance(unified, UnifiedReports)
        # Two scenario units flow into every per-unit section.
        assert [u.name for u in unified.execution_stats.units] == ['s1', 's2']
        assert [u.symbol for u in unified.execution_stats.units] == ['EURUSD', 'GBPUSD']
        # The summed totals are the cross-section measure both console + ledger read.
        assert unified.execution_stats.totals.orders_sent == 8
        assert unified.execution_stats.totals.orders_executed == 7

    def test_session_single_unit(self, tmp_path):
        io_dir = tmp_path / 'io'
        result = AutoTraderResult(execution_stats=_stats(7, 6, 1, 4))
        unified = SharedReportCoordinator.derive_and_persist(
            run_units_from_session(result, 'my_profile', 'BTCUSD'), io_dir)
        for name in _EXPECTED_FILES:
            assert (io_dir / name).exists(), f'missing artifact: {name}'
        assert len(unified.execution_stats.units) == 1
        assert unified.execution_stats.units[0].symbol == 'BTCUSD'
        assert unified.execution_stats.totals.orders_executed == 6
