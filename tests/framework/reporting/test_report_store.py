"""
Report Store Tests (#391).

The store resolves persisted trade-history artifacts under a logs tree and applies
the shared filter. Tested against a temporary logs directory with fixture artifacts —
no run required.
"""

from pathlib import Path

from python.framework.reporting.run_reports.execution_stats_report_io import (
    write_execution_stats_csv, write_execution_stats_report)
from python.framework.reporting.run_reports.order_history_report_io import write_order_history_report
from python.framework.reporting.run_reports.pending_orders_report_io import write_pending_orders_report
from python.framework.reporting.run_reports.portfolio_report_io import write_portfolio_report
from python.framework.reporting.run_reports.report_store import ReportStore
from python.framework.reporting.run_reports.scenario_details_report_io import write_scenario_details_report
from python.framework.reporting.run_reports.trade_history_report_io import (
    write_trade_history_csv, write_trade_history_report)
from python.framework.types.api.report_types import (
    ActiveOrderRow, ExecutionStatsReport, ExecutionStatsRow, ExecutionStatsTotals,
    OrderHistoryReport, OrderHistoryRow, PendingOrdersReport, PendingOrdersUnitRow,
    PortfolioAggregateRow, PortfolioReport, PortfolioUnitRow,
    ScenarioDetailsReport, ScenarioDetailsRow,
    TradeAnalytics, TradeHistoryReport, TradeHistoryRow)

_ZERO_ANALYTICS = TradeAnalytics(
    expectancy=0.0, avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=0,
    avg_mae_winners=0.0, avg_mae_losers=0.0, avg_mfe_losers=0.0)


def _row(position_id: str, symbol: str, close_reason: str, entry_time: str) -> TradeHistoryRow:
    return TradeHistoryRow(
        position_id=position_id, symbol=symbol, direction='long', lots=0.1,
        entry_price=1.10, entry_time=entry_time, exit_price=1.11, exit_time=entry_time,
        duration_s=600.0, close_reason=close_reason,
        gross_pnl=1.0, total_fees=0.2, net_pnl=0.8,
    )


def _report() -> TradeHistoryReport:
    rows = [
        _row('p1', 'EURUSD', 'tp_triggered', '2025-10-13T08:00:00+00:00'),
        _row('p2', 'GBPUSD', 'sl_triggered', '2025-10-13T09:00:00+00:00'),
        _row('p3', 'EURUSD', 'sl_triggered', '2025-10-13T10:00:00+00:00'),
    ]
    return TradeHistoryReport(
        trades=rows, count=len(rows), symbols=['EURUSD', 'GBPUSD'], analytics=[_ZERO_ANALYTICS])


def _write_run(logs_root: Path, group: str, owner: str, run_id: str) -> None:
    run_dir = logs_root / group / owner / run_id
    run_dir.mkdir(parents=True)
    write_trade_history_report(_report(), run_dir)


class TestResolveRead:
    def test_reads_a_run(self, tmp_path):
        _write_run(tmp_path, 'scenario_sets', 'my_set', '20260615_120000')
        report = ReportStore(tmp_path).get_trade_history('20260615_120000')
        assert report is not None
        assert report.count == 3

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_trade_history('does_not_exist') is None

    def test_resolves_autotrader_run(self, tmp_path):
        _write_run(tmp_path, 'autotrader', 'my_profile', '20260615_130000')
        report = ReportStore(tmp_path).get_trade_history('20260615_130000')
        assert report is not None and report.count == 3


class TestFilter:
    def test_filter_by_symbol(self, tmp_path):
        _write_run(tmp_path, 'scenario_sets', 'my_set', '20260615_120000')
        report = ReportStore(tmp_path).get_trade_history('20260615_120000', symbol='GBPUSD')
        assert report.count == 1 and report.trades[0].position_id == 'p2'

    def test_filter_by_close_reason(self, tmp_path):
        _write_run(tmp_path, 'scenario_sets', 'my_set', '20260615_120000')
        report = ReportStore(tmp_path).get_trade_history(
            '20260615_120000', close_reason='sl_triggered')
        assert {r.position_id for r in report.trades} == {'p2', 'p3'}


class TestListRuns:
    def test_lists_both_groups_newest_first(self, tmp_path):
        _write_run(tmp_path, 'scenario_sets', 'my_set', '20260615_120000')
        _write_run(tmp_path, 'autotrader', 'my_profile', '20260615_130000')
        assert ReportStore(tmp_path).list_runs() == ['20260615_130000', '20260615_120000']


class TestCsv:
    """CSV mirror of the model — same columns as JSON / API."""

    def test_csv_header_and_rows(self, tmp_path):
        write_trade_history_csv(_report(), tmp_path)
        lines = (tmp_path / 'trade_history.csv').read_text().splitlines()
        assert lines[0].startswith('position_id,symbol,direction')
        assert len(lines) == 1 + 3                 # header + 3 rows
        assert 'EURUSD' in lines[1]


def _order_row(order_id: str, symbol: str, status: str) -> OrderHistoryRow:
    return OrderHistoryRow(
        order_id=order_id, position_id=f'pos_{order_id}', symbol=symbol,
        direction='long', action='open', status=status,
        requested_lots=0.1, executed_lots=0.1, executed_price=1.10,
        execution_time='2025-10-13T08:00:00+00:00',
        commission=0.2, swap=0.0, slippage_points=1.0,
        rejection_reason='', rejection_message='',
    )


def _order_report() -> OrderHistoryReport:
    rows = [
        _order_row('o1', 'EURUSD', 'executed'),
        _order_row('o2', 'GBPUSD', 'rejected'),
    ]
    return OrderHistoryReport(orders=rows, count=len(rows), symbols=['EURUSD', 'GBPUSD'])


def _portfolio_report() -> PortfolioReport:
    unit = PortfolioUnitRow(
        name='s1', symbol='EURUSD', currency='USD', total_trades=10,
        winning_trades=6, losing_trades=4, win_rate=0.6, profit_factor=2.5,
        total_profit=100.0, total_loss=40.0, net_profit=60.0, max_drawdown=12.0,
        total_fees=5.0)
    agg = PortfolioAggregateRow(
        currency='USD', unit_count=1, total_trades=10, winning_trades=6,
        losing_trades=4, win_rate=0.6, profit_factor=2.5, total_profit=100.0,
        total_loss=40.0, net_profit=60.0, max_drawdown=12.0, total_fees=5.0)
    return PortfolioReport(units=[unit], aggregates=[agg])


class TestOrderHistory:
    def test_reads_and_filters(self, tmp_path):
        run_dir = tmp_path / 'scenario_sets' / 'my_set' / '20260615_120000'
        run_dir.mkdir(parents=True)
        write_order_history_report(_order_report(), run_dir)

        full = ReportStore(tmp_path).get_order_history('20260615_120000')
        assert full.count == 2

        rejected = ReportStore(tmp_path).get_order_history(
            '20260615_120000', status='rejected')
        assert rejected.count == 1 and rejected.orders[0].order_id == 'o2'

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_order_history('nope') is None


class TestPortfolio:
    def test_reads_portfolio(self, tmp_path):
        run_dir = tmp_path / 'autotrader' / 'my_profile' / '20260615_130000'
        run_dir.mkdir(parents=True)
        write_portfolio_report(_portfolio_report(), run_dir)

        report = ReportStore(tmp_path).get_portfolio('20260615_130000')
        assert report is not None
        assert len(report.units) == 1 and report.units[0].net_profit == 60.0
        assert report.aggregates[0].currency == 'USD'

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_portfolio('nope') is None


def _execution_stats_report() -> ExecutionStatsReport:
    unit = ExecutionStatsRow(
        name='s1', symbol='EURUSD', orders_sent=5, orders_executed=4,
        orders_rejected=1, sl_tp_triggered=2)
    totals = ExecutionStatsTotals(
        orders_sent=5, orders_executed=4, orders_rejected=1, sl_tp_triggered=2)
    return ExecutionStatsReport(units=[unit], totals=totals)


class TestExecutionStats:
    def test_reads_execution_stats(self, tmp_path):
        run_dir = tmp_path / 'scenario_sets' / 'my_set' / '20260615_120000'
        run_dir.mkdir(parents=True)
        write_execution_stats_report(_execution_stats_report(), run_dir)

        report = ReportStore(tmp_path).get_execution_stats('20260615_120000')
        assert report is not None
        assert report.units[0].sl_tp_triggered == 2
        assert report.totals.orders_executed == 4

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_execution_stats('nope') is None

    def test_csv_header_and_rows(self, tmp_path):
        write_execution_stats_csv(_execution_stats_report(), tmp_path)
        lines = (tmp_path / 'execution_stats.csv').read_text().splitlines()
        assert lines[0].startswith('name,symbol,orders_sent')
        assert len(lines) == 1 + 1                 # header + 1 unit row
        assert 'EURUSD' in lines[1]


def _pending_orders_report() -> PendingOrdersReport:
    unit = PendingOrdersUnitRow(
        name='s1', symbol='EURUSD', total_resolved=3, total_filled=2,
        total_force_closed=1, avg_latency_ms=42.0, min_latency_ms=21.0, max_latency_ms=60.0,
        active_limit_orders=[ActiveOrderRow(
            order_id='L1', order_type='limit', direction='long', lots=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.11)])
    return PendingOrdersReport(units=[unit])


class TestPendingOrders:
    def test_reads_pending_orders(self, tmp_path):
        run_dir = tmp_path / 'scenario_sets' / 'my_set' / '20260615_120000'
        run_dir.mkdir(parents=True)
        write_pending_orders_report(_pending_orders_report(), run_dir)

        report = ReportStore(tmp_path).get_pending_orders('20260615_120000')
        assert report is not None
        u = report.units[0]
        assert u.total_resolved == 3 and u.avg_latency_ms == 42.0
        assert u.active_limit_orders[0].order_id == 'L1'

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_pending_orders('nope') is None


def _scenario_details_report() -> ScenarioDetailsReport:
    return ScenarioDetailsReport(units=[
        ScenarioDetailsRow(
            name='s1', symbol='EURUSD', data_source='mt5', status='success',
            ticks_processed=15000, buy_signals=296, sell_signals=263, worker_count=2),
        ScenarioDetailsRow(
            name='bad', symbol='BTCUSD', data_source='kraken_spot', status='failed',
            error_type='ValidationError', error_message='start before data'),
    ])


class TestScenarioDetails:
    def test_reads_scenario_details(self, tmp_path):
        run_dir = tmp_path / 'scenario_sets' / 'my_set' / '20260615_120000'
        run_dir.mkdir(parents=True)
        write_scenario_details_report(_scenario_details_report(), run_dir)

        report = ReportStore(tmp_path).get_scenario_details('20260615_120000')
        assert report is not None
        assert [u.status for u in report.units] == ['success', 'failed']
        assert report.units[0].buy_signals == 296

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_scenario_details('nope') is None
