"""
Shared Report Coordinator Tests (#403).

`SharedReportCoordinator.derive_and_persist` is the units-derived DERIVE+PERSIST core both
pipelines delegate to. Tested against a real BatchExecutionSummary / ProcessResult /
SingleScenario (sim) and a real AutoTraderResult (live) — not stand-ins — so it exercises the
actual write-path: all 9 sections' artifacts land in the io/ dir and the returned
UnifiedReports carries the same models the caller reuses for console + ledger.
"""

from datetime import datetime, timezone

from python.framework.reporting.builders.run_unit import (
    run_units_from_batch,
    run_units_from_session,
    unit_roster_from_session,
)
from python.framework.reporting.builders.unified_reports import UnifiedReports
from python.framework.reporting.shared_report_coordinator import SharedReportCoordinator
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)

# Every section writes these artifacts into the io/ dir (json for all 9, csv for three).
_EXPECTED_FILES = [
    'trade_history.json', 'trade_history.csv',
    'order_history.json', 'order_history.csv',
    'portfolio.json',
    'pending_orders.json',
    'execution_stats.json', 'execution_stats.csv',
    'run_summary.json',
    'worker_decision.json',
    'signal.json',
    'feed_stability.json',
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
    """The shared core: writes the 8 sections + returns the populated DTO."""

    def test_writes_all_artifacts_batch(self, tmp_path):
        io_dir = tmp_path / 'io'
        SharedReportCoordinator.derive_and_persist(_RUN_ID, run_units_from_batch(_batch()), io_dir)
        for name in _EXPECTED_FILES:
            assert (io_dir / name).exists(), f'missing artifact: {name}'

    def test_creates_io_dir_if_missing(self, tmp_path):
        # A nested, not-yet-existing io/ path must be created by the coordinator.
        io_dir = tmp_path / 'run' / 'io'
        SharedReportCoordinator.derive_and_persist(_RUN_ID, run_units_from_batch(_batch()), io_dir)
        assert io_dir.is_dir()
        assert (io_dir / 'run_summary.json').exists()

    def test_returns_populated_unified_reports(self, tmp_path):
        unified = SharedReportCoordinator.derive_and_persist(_RUN_ID, 
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
        unified = SharedReportCoordinator.derive_and_persist(_RUN_ID, 
            run_units_from_session(result, 'my_profile', 'BTCUSD'), io_dir,
            roster=unit_roster_from_session(result, 'my_profile'))
        for name in _EXPECTED_FILES:
            assert (io_dir / name).exists(), f'missing artifact: {name}'
        assert len(unified.execution_stats.units) == 1
        assert unified.execution_stats.units[0].symbol == 'BTCUSD'
        assert unified.execution_stats.totals.orders_executed == 6
        # A session's roster has the simulation's shape. This one carries no portfolio
        # statistics — a startup abort — so it is declared and ABSENT, not silently uncounted.
        summary = unified.run_summary
        assert (summary.units_declared, summary.units_disabled, summary.unit_count) == (1, 0, 0)
        assert [row.name for row in summary.units_absent] == ['my_profile']
        assert summary.units_declared == (
            summary.units_disabled + len(summary.units_absent) + summary.unit_count)

    def test_a_session_that_ran_is_counted_not_absent(self):
        """The ordinary live case: one declared, one counted, nothing missing."""
        result = AutoTraderResult(execution_stats=_stats(7, 6, 1, 4), portfolio_stats=PortfolioStats(
            broker_type=BrokerType.KRAKEN_SPOT, total_trades=0, total_long_trades=0,
            total_short_trades=0, winning_trades=0, losing_trades=0, total_profit=0.0,
            total_loss=0.0, account_max_drawdown=0.0, max_equity=1000.0,
            account_max_drawdown_pct=0.0, win_rate=0.0, profit_factor=None,
            total_spread_cost=0.0, total_commission=0.0, total_swap=0.0, maker_fee=0.0,
            taker_fee=0.0, total_fees=0.0, currency='USD', broker_name='Kraken',
            current_conversion_rate=1.0, current_balance=1000.0, initial_balance=1000.0))

        roster = unit_roster_from_session(result, 'my_profile')

        assert (roster.declared, roster.disabled, roster.absent) == (1, 0, [])
