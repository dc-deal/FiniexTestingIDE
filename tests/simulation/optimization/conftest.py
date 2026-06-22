"""
Shared fixtures for the Parameter Optimization suite (#390).

Factory fixtures build the REAL report/ledger types (RunSummary, RunProvenance) — never
SimpleNamespace stand-ins — so the tests exercise the exact objects the pipeline writes.
"""

from datetime import datetime, timezone

import pytest

from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import RunSummary, RunSummaryCurrency
from python.framework.types.run_results_types import RunProvenance


@pytest.fixture
def tmp_ledger(tmp_path):
    """A RunResultsLedger pointed at a throwaway directory (no data/ pollution, §34)."""
    return RunResultsLedger(tmp_path / 'run_results')


@pytest.fixture
def make_run_summary():
    """Factory: a RunSummary with one currency row carrying the given KPIs."""
    def _make(currency='USD', net_pnl=0.0, expectancy=0.0, profit_factor=0.0,
              win_rate=0.0, max_drawdown=0.0, total_trades=0,
              orders_sent=0, orders_executed=0):
        return RunSummary(
            currencies=[RunSummaryCurrency(
                currency=currency, net_pnl=net_pnl, profit_factor=profit_factor,
                win_rate=win_rate, max_drawdown=max_drawdown, total_fees=0.0,
                total_trades=total_trades, winning_trades=0, losing_trades=0,
                expectancy=expectancy, avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=0)],
            orders_sent=orders_sent, orders_executed=orders_executed,
            orders_rejected=0, sl_tp_triggered=0, unit_count=1)
    return _make


@pytest.fixture
def make_provenance():
    """Factory: a RunProvenance with sensible defaults + overridable status / sweep tagging."""
    def _make(param_hash='hash', run_id='20260101_000000',
              scenario_set_name='set', sweep_id=None, sweep_params=None,
              status='ok', error=None, sweep_objective=None, sweep_maximize=None,
              run_timestamp=None):
        return RunProvenance(
            param_hash=param_hash, status=status, error=error, run_id=run_id,
            run_timestamp=run_timestamp or datetime(2026, 1, 1, tzinfo=timezone.utc),
            scenario_set_name=scenario_set_name, git_commit='abc1234',
            git_branch='main', git_dirty=False,
            decision_logic_type='CORE/aggressive_trend', decision_version='1.0.0',
            worker_versions={'rsi_fast': '1.0.0'}, config_snapshot='{}',
            symbols=['BTCUSD'], data_broker_type='kraken_spot',
            sweep_id=sweep_id, sweep_params=sweep_params,
            sweep_objective=sweep_objective, sweep_maximize=sweep_maximize)
    return _make
