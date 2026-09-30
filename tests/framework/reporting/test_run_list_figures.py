"""
FiniexTestingIDE - Run List Figures

The run list carries what each run DID, joined from the run-results ledger in one read. Three
states are held apart, because a consumer renders each differently: a run the ledger holds nothing
for (still going, died before its close, never reported) carries None; a run that closed without
figures carries an empty list; a run with figures carries one entry per account currency.
"""

from datetime import datetime, timezone
from pathlib import Path

from python.framework.reporting.store.report_store import ReportStore
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_list_figures import get_run_list_figures
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import RunHeader, RunSummary, RunSummaryCurrency
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.run_results_types import RunProvenance

_FIGURES = '20260929_100000_aaaaaaaa'
_TWO_CURRENCIES = '20260929_110000_bbbbbbbb'
_NOTHING_PRODUCED = '20260929_120000_cccccccc'
_NOT_IN_LEDGER = '20260929_130000_dddddddd'


def _currency(currency: str, net_pnl: float, total_trades: int) -> RunSummaryCurrency:
    return RunSummaryCurrency(
        currency=currency, net_pnl=net_pnl, profit_factor=0.0, win_rate=0.0,
        account_max_drawdown=0.0, total_fees=0.0, gross_profit=0.0, gross_loss=0.0,
        total_trades=total_trades, winning_trades=0, losing_trades=0, expectancy=0.0,
        avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=0)


def _summary(run_id: str, *currencies: RunSummaryCurrency) -> RunSummary:
    return RunSummary(run_id=run_id, currencies=list(currencies), orders_sent=0,
                      orders_executed=0, orders_rejected=0, sl_tp_triggered=0, unit_count=1)


def _provenance(run_id: str, **counted) -> RunProvenance:
    return RunProvenance(
        param_hash='hash', status='ok', error=None, run_id=run_id,
        run_timestamp=datetime(2026, 9, 29, tzinfo=timezone.utc), scenario_set_name='my_set',
        app_version='1.4.0', git_commit='abc1234', git_branch='dev', git_dirty=False,
        decision_logic_type='CORE/aggressive_trend', decision_version='1.0.0',
        worker_versions={}, strategy_config_json='{}', symbols=['EURUSD'],
        data_broker_type='mt5', **counted)


def _tree(tmp_path: Path) -> ReportStore:
    """
    Four runs in the index; three of them in the ledger.

    Args:
        tmp_path: pytest temporary directory

    Returns:
        A store pointed at this tree's index and ledger
    """
    index = RunIndex(tmp_path / 'index.parquet')
    for run_id in (_FIGURES, _TWO_CURRENCIES, _NOTHING_PRODUCED, _NOT_IN_LEDGER):
        run_dir = tmp_path / 'runs' / run_id
        run_dir.mkdir(parents=True)
        index.register_run(RunHeader(run_id=run_id, run_type='simulation', run_name='my_set',
                                     start_time=datetime(2026, 9, 29, tzinfo=timezone.utc)),
                           run_dir)
    ledger = RunResultsLedger(tmp_path / 'ledger')
    ledger.append(_summary(_FIGURES, _currency('USD', -412.37, 530)),
                  _provenance(_FIGURES, run_outcome=RunOutcome.SUCCESS, error_count=0,
                              warning_count=2, log_warning_count=118))
    ledger.append(_summary(_TWO_CURRENCIES, _currency('USD', 10.0, 3), _currency('EUR', -4.0, 2)),
                  _provenance(_TWO_CURRENCIES, run_outcome=RunOutcome.FINISHED_WITH_ERRORS, error_count=5))
    ledger.append(_summary(_NOTHING_PRODUCED),
                  _provenance(_NOTHING_PRODUCED, run_outcome=RunOutcome.FAILED, error_count=1))
    return ReportStore(tmp_path / 'index.parquet', tmp_path / 'ledger')


class TestTheListSaysWhatARunDid:

    def test_a_run_with_figures_carries_them_and_its_counts(self, tmp_path):
        runs = {run.run_id: run for run in _tree(tmp_path).list_runs_with_results()}
        run = runs[_FIGURES]
        assert [(r.currency, r.net_pnl, r.total_trades) for r in run.results] == [
            ('USD', -412.37, 530)]
        assert (run.run_outcome, run.error_count, run.warning_count, run.log_warning_count) == (
            RunOutcome.SUCCESS, 0, 2, 118)

    def test_each_account_currency_is_its_own_entry(self, tmp_path):
        runs = {run.run_id: run for run in _tree(tmp_path).list_runs_with_results()}
        assert sorted((r.currency, r.net_pnl) for r in runs[_TWO_CURRENCIES].results) == [
            ('EUR', -4.0), ('USD', 10.0)]

    def test_a_run_that_produced_nothing_carries_an_empty_list(self, tmp_path):
        # Its one ledger row is the figureless error row; its zeros are not figures.
        runs = {run.run_id: run for run in _tree(tmp_path).list_runs_with_results()}
        assert runs[_NOTHING_PRODUCED].results == []
        assert runs[_NOTHING_PRODUCED].run_outcome is RunOutcome.FAILED

    def test_a_run_the_ledger_does_not_know_carries_none(self, tmp_path):
        runs = {run.run_id: run for run in _tree(tmp_path).list_runs_with_results()}
        run = runs[_NOT_IN_LEDGER]
        assert run.results is None and run.run_outcome is None and run.error_count is None

    def test_a_count_nobody_recorded_is_none_not_zero(self, tmp_path):
        runs = {run.run_id: run for run in _tree(tmp_path).list_runs_with_results()}
        assert runs[_TWO_CURRENCIES].warning_count is None


class TestTheLedgerIsReadOncePerChange:

    def test_a_run_appended_later_reaches_the_list(self, tmp_path):
        store = _tree(tmp_path)
        assert _NOT_IN_LEDGER not in get_run_list_figures(tmp_path / 'ledger')
        RunResultsLedger(tmp_path / 'ledger').append(
            _summary(_NOT_IN_LEDGER, _currency('USD', 1.0, 1)), _provenance(_NOT_IN_LEDGER))
        runs = {run.run_id: run for run in store.list_runs_with_results()}
        assert [r.net_pnl for r in runs[_NOT_IN_LEDGER].results] == [1.0]

    def test_an_unchanged_ledger_is_not_read_again(self, tmp_path, monkeypatch):
        _tree(tmp_path)
        first = get_run_list_figures(tmp_path / 'ledger')
        get_run_list_figures(tmp_path / 'ledger')    # settles a stamp moved by an index rebuild

        def _refuse(*_args, **_kwargs):
            raise AssertionError('the ledger was read although nothing changed')
        monkeypatch.setattr(RunResultsLedger, 'read_rows', _refuse)
        assert get_run_list_figures(tmp_path / 'ledger') == first

    def test_no_ledger_is_no_figures(self, tmp_path):
        assert get_run_list_figures(tmp_path / 'no_ledger_here') == {}
