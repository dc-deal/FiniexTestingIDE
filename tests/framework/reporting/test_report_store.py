"""
Report Store Tests (#391).

The store resolves persisted trade-history artifacts under a logs tree and applies
the shared filter. Tested against a temporary logs directory with fixture artifacts —
no run required.
"""

from pathlib import Path

from python.framework.reporting.io.aggregated_portfolio_report_io import write_aggregated_portfolio_report
from python.framework.reporting.io.broker_report_io import write_broker_report
from python.framework.reporting.io.execution_stats_report_io import (
    write_execution_stats_csv, write_execution_stats_report)
from python.framework.reporting.io.order_history_report_io import write_order_history_report
from python.framework.reporting.io.pending_orders_report_io import write_pending_orders_report
from python.framework.reporting.io.portfolio_report_io import write_portfolio_report
from python.framework.reporting.store.report_store import IO_SUBDIR, ReportStore
from python.framework.reporting.io.run_summary_io import write_run_summary
from python.framework.reporting.io.scenario_details_report_io import write_scenario_details_report
from python.framework.reporting.io.trade_history_report_io import (
    write_trade_history_csv, write_trade_history_report)
from python.framework.reporting.io.warnings_errors_report_io import write_warnings_errors_report
from python.framework.types.api.report_types import (
    ActiveOrderRow, AggregatedPortfolioCurrency, AggregatedPortfolioReport, AggregatedPortfolioRow,
    BrokerInfoRow, BrokerReport, BrokerSymbolRow,
    ExecutionStatsReport, ExecutionStatsRow, ExecutionStatsTotals,
    OrderHistoryReport, OrderHistoryRow, PendingOrdersReport, PendingOrdersUnitRow,
    PortfolioAggregateRow, PortfolioReport, PortfolioUnitRow, RunSummary, RunSummaryCurrency,
    ScenarioDetailsReport, ScenarioDetailsRow,
    TradeAnalytics, TradeHistoryReport, TradeHistoryRow,
    UnitErrorRow, WarningRow, WarningsErrorsOutcome, WarningsErrorsReport)

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
    # Artifacts live in the run's io/ subfolder (#396 housekeeping)
    io_dir = logs_root / group / owner / run_id / IO_SUBDIR
    io_dir.mkdir(parents=True)
    write_trade_history_report(_report(), io_dir)


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
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        write_order_history_report(_order_report(), run_dir / IO_SUBDIR)

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
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        write_portfolio_report(_portfolio_report(), run_dir / IO_SUBDIR)

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
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        write_execution_stats_report(_execution_stats_report(), run_dir / IO_SUBDIR)

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
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        write_pending_orders_report(_pending_orders_report(), run_dir / IO_SUBDIR)

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
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        write_scenario_details_report(_scenario_details_report(), run_dir / IO_SUBDIR)

        report = ReportStore(tmp_path).get_scenario_details('20260615_120000')
        assert report is not None
        assert [u.status for u in report.units] == ['success', 'failed']
        assert report.units[0].buy_signals == 296

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_scenario_details('nope') is None


def _run_summary() -> RunSummary:
    return RunSummary(
        currencies=[RunSummaryCurrency(
            currency='USD', net_pnl=60.0, profit_factor=2.5, win_rate=0.6, max_drawdown=12.0,
            total_fees=5.0, total_trades=10, winning_trades=6, losing_trades=4,
            expectancy=0.5, avg_win_r=2.0, avg_loss_r=-1.0, r_trade_count=4)],
        orders_sent=5, orders_executed=4, orders_rejected=1, sl_tp_triggered=2, unit_count=1)


class TestRunSummary:
    def test_reads_run_summary(self, tmp_path):
        run_dir = tmp_path / 'scenario_sets' / 'my_set' / '20260615_120000'
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        write_run_summary(_run_summary(), run_dir / IO_SUBDIR)

        rs = ReportStore(tmp_path).get_run_summary('20260615_120000')
        assert rs is not None
        assert rs.currencies[0].expectancy == 0.5
        assert rs.orders_executed == 4 and rs.unit_count == 1

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_run_summary('nope') is None


def _broker_report() -> BrokerReport:
    return BrokerReport(units=[BrokerInfoRow(
        broker_type='kraken_spot', market_type='crypto', company='Kraken',
        config_hash='abcd1234', scenarios=['btc_run'],
        symbols=[BrokerSymbolRow(symbol='BTCUSD', base_currency='BTC', quote_currency='USD')])])


def _warnings_errors_report() -> WarningsErrorsReport:
    return WarningsErrorsReport(
        warnings=[WarningRow(tier='major', scope='run', message='DEBUG MODE')],
        errors=[UnitErrorRow(name='bad', symbol='BTCUSD', error_type='ValidationError')],
        outcome=WarningsErrorsOutcome(failed_count=1, total_units=2))


class TestWarningsErrors:
    def test_reads_warnings_errors(self, tmp_path):
        run_dir = tmp_path / 'scenario_sets' / 'my_set' / '20260615_120000'
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        write_warnings_errors_report(_warnings_errors_report(), run_dir / IO_SUBDIR)

        report = ReportStore(tmp_path).get_warnings_errors('20260615_120000')
        assert report is not None
        assert report.warnings[0].message == 'DEBUG MODE'
        assert report.errors[0].name == 'bad'
        assert report.outcome.failed_count == 1

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_warnings_errors('nope') is None


def _aggregated_portfolio_report() -> AggregatedPortfolioReport:
    headline = PortfolioAggregateRow(
        currency='USD', unit_count=2, total_trades=4, winning_trades=2, losing_trades=2,
        win_rate=0.5, profit_factor=2.0, total_profit=100.0, total_loss=50.0, net_profit=50.0,
        max_drawdown=12.0, total_fees=5.0)
    row = AggregatedPortfolioRow(headline=headline, initial_balance=2000.0, final_balance=2050.0)
    return AggregatedPortfolioReport(currencies=[AggregatedPortfolioCurrency(
        currency='USD', scenario_count=2, scenario_names=['s1', 's2'], combined=row)])


class TestAggregatedPortfolio:
    def test_reads_aggregated_portfolio(self, tmp_path):
        run_dir = tmp_path / 'scenario_sets' / 'my_set' / '20260615_120000'
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        write_aggregated_portfolio_report(_aggregated_portfolio_report(), run_dir / IO_SUBDIR)

        report = ReportStore(tmp_path).get_aggregated_portfolio('20260615_120000')
        assert report is not None
        cur = report.currencies[0]
        assert cur.currency == 'USD' and cur.combined.headline.total_trades == 4
        assert cur.combined.initial_balance == 2000.0

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_aggregated_portfolio('nope') is None


class TestBroker:
    def test_reads_broker(self, tmp_path):
        run_dir = tmp_path / 'scenario_sets' / 'my_set' / '20260615_120000'
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        write_broker_report(_broker_report(), run_dir / IO_SUBDIR)

        report = ReportStore(tmp_path).get_broker('20260615_120000')
        assert report is not None
        assert report.units[0].broker_type == 'kraken_spot'
        assert report.units[0].symbols[0].symbol == 'BTCUSD'

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(tmp_path).get_broker('nope') is None
