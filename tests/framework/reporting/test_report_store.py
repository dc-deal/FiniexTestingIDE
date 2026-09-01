"""
Report Store Tests (#391).

The store resolves persisted trade-history artifacts under a logs tree and applies
the shared filter. Tested against a temporary logs directory with fixture artifacts —
no run required.
"""

from datetime import datetime, timezone
from pathlib import Path

from python.framework.reporting.io.artifact_specs import (
    AGGREGATED_PORTFOLIO_ARTIFACT,
    BROKER_ARTIFACT,
    EXECUTION_STATS_ARTIFACT,
    ORDER_HISTORY_ARTIFACT,
    PENDING_ORDERS_ARTIFACT,
    PORTFOLIO_ARTIFACT,
    RUN_SUMMARY_ARTIFACT,
    SCENARIO_DETAILS_ARTIFACT,
    TRADE_HISTORY_ARTIFACT,
    WARNINGS_ERRORS_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.reporting.io.report_csv_io import (
    write_execution_stats_csv,
    write_trade_history_csv,
)
from python.framework.reporting.store.report_store import IO_SUBDIR, ReportStore
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import (
    ActiveOrderRow,
    AggregatedPortfolioCurrency,
    AggregatedPortfolioReport,
    AggregatedPortfolioRow,
    BrokerInfoRow,
    BrokerReport,
    BrokerSymbolRow,
    ExecutionStatsReport,
    ExecutionStatsRow,
    ExecutionStatsTotals,
    OrderHistoryReport,
    OrderHistoryRow,
    PendingOrdersReport,
    PendingOrdersUnitRow,
    PortfolioAggregateRow,
    PortfolioReport,
    PortfolioUnitRow,
    RunHeader,
    RunSummary,
    RunSummaryCurrency,
    ScenarioDetailsReport,
    ScenarioDetailsRow,
    TradeAnalytics,
    TradeHistoryReport,
    TradeHistoryRow,
    UnitErrorRow,
    WarningRow,
    WarningsErrorsOutcome,
    WarningsErrorsReport,
)
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.log_layout_types import (
    RUN_TYPE_LIVE,
    RUN_TYPE_SIMULATION,
)

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

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
    return TradeHistoryReport(run_id=_RUN_ID, 
        trades=rows, count=len(rows), symbols=['EURUSD', 'GBPUSD'], analytics=[_ZERO_ANALYTICS])


def _run_logs(root: Path) -> RunLogPaths:
    """The two run-type roots under a tmp logs tree."""
    return RunLogPaths(simulation=root / 'simulation', live=root / 'live')


def _base(root: Path, run_type: str, sweep_id: str = '') -> Path:
    """
    Where a run of this type lands.

    Args:
        root: The tmp logs tree
        run_type: 'simulation' or 'live'
        sweep_id: The owning sweep, when the run is one of its combinations

    Returns:
        The directory the run's owner folder sits in
    """
    roots = _run_logs(root)
    if run_type == RUN_TYPE_LIVE:
        return roots.live
    return roots.sweeps / sweep_id if sweep_id else roots.simulation


def _planted_run(root: Path, category: str, owner: str, run_id: str) -> Path:
    """
    A run directory the store can actually find: artifacts AND an index row.

    The store resolves through the index now, so planting only a directory plants a run nobody
    can look up — which is the honest behaviour, not a test inconvenience.

    Args:
        root: The tmp logs tree
        category: Its group
        owner: The scenario set / profile name
        run_id: The run's identity

    Returns:
        The run directory, with io/ created
    """
    run_dir = _base(root, category) / owner / run_id
    (run_dir / IO_SUBDIR).mkdir(parents=True)
    _index(root).register_run(_run_header(run_id, category, owner), run_dir)
    return run_dir


def _index_path(root: Path) -> Path:
    """The tmp tree's OWN index — the store must be pointed at it, not at the real one."""
    return root / 'index.parquet'


def _index(root: Path) -> RunIndex:
    return RunIndex(_index_path(root))


def _run_header(run_id: str, category: str, owner: str, parent: str = None) -> RunHeader:
    return RunHeader(run_id=run_id, run_type=category, run_name=owner, parent_id=parent,
                     start_time=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc))


def _write_run(logs_root: Path, category: str, owner: str, run_id: str,
               sweep_id: str = '') -> None:
    """
    Write one run's artifacts into its category.

    Args:
        logs_root: The logs tree root
        category: 'simulation' or 'live'
        owner: The scenario set / profile name
        run_id: The run's directory name
        sweep_id: The owning sweep, for the sweeps category
    """
    # Artifacts live in the run's io/ subfolder (#396 housekeeping)
    run_dir = _base(logs_root, category, sweep_id) / owner / run_id
    io_dir = run_dir / IO_SUBDIR
    io_dir.mkdir(parents=True)
    write_artifact(_report(), io_dir, TRADE_HISTORY_ARTIFACT)
    _index(logs_root).register_run(
        _run_header(run_id, category, owner, sweep_id or None), run_dir)
    # The artifacts exist, so the row lists them — the index is told, never inferred.
    _index(logs_root).record_artifacts(run_id, run_dir)


class TestTheThreeCategories:
    """
    A run belongs to exactly one category, and the category IS its `group`. The index lists
    EVERY run of every category and says per row whether reports exist — an index that silently
    omits runs is its own surprise, and `has_reports` lets a consumer decide what to show.

    Swept runs were once invisible to the whole store: a fixed-depth lookup missed them, so a
    parameter sweep's results could not be read over the API at all.
    """

    _SWEEP = 'sweep_20260829_184006'

    def test_depth_alone_does_not_hide_a_run(self, tmp_path):
        """The lookup must not assume a fixed depth under a category root."""
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        runs = ReportStore(_index_path(tmp_path)).list_runs()
        assert [r.run_id for r in runs] == ['20260615_120000_aaaaaaaa']
        assert runs[0].group == RUN_TYPE_SIMULATION and runs[0].name == 'my_set'

    def test_a_sweep_combination_stays_addressable(self, tmp_path):
        """Excluded from the INDEX, never from resolution — the index is a browse aid."""
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set__c000', '20260829_184007_cccccccc',
                   sweep_id=self._SWEEP)
        assert ReportStore(_index_path(tmp_path)).get_trade_history('20260829_184007_cccccccc') is not None

    def test_a_run_without_reports_is_listed_and_flagged(self, tmp_path):
        """A test session writes logs and no artifacts — it exists, and the row says so."""
        # A log-only session still writes its header at start — that is what makes it a run
        # the index knows. Only its artifacts are missing.
        bare = _planted_run(tmp_path, RUN_TYPE_LIVE, 'probe_test', '20260829_213636_dddddddd')
        (bare / 'session_logs').mkdir(parents=True)
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')

        runs = {r.run_id: r for r in ReportStore(_index_path(tmp_path)).list_runs()}
        assert set(runs) == {'20260829_213636_dddddddd', '20260615_120000_aaaaaaaa'}
        assert runs['20260829_213636_dddddddd'].has_reports is False
        assert runs['20260615_120000_aaaaaaaa'].has_reports is True

    def test_all_three_categories_side_by_side(self, tmp_path):
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'plain_set', '20260615_120000_aaaaaaaa')
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set__c000', '20260829_184007_cccccccc',
                   sweep_id=self._SWEEP)
        _write_run(tmp_path, RUN_TYPE_LIVE, 'my_profile', '20260615_130000_bbbbbbbb')
        store = ReportStore(_index_path(tmp_path))
        runs = {r.run_id: r for r in store.list_runs()}
        assert set(runs) == {'20260615_120000_aaaaaaaa', '20260829_184007_cccccccc', '20260615_130000_bbbbbbbb'}
        assert runs['20260829_184007_cccccccc'].group == RUN_TYPE_SIMULATION
        assert runs['20260615_120000_aaaaaaaa'].group == RUN_TYPE_SIMULATION
        assert runs['20260615_130000_bbbbbbbb'].group == RUN_TYPE_LIVE
        for run_id in runs:
            assert store.get_trade_history(run_id) is not None


class TestResolveRead:
    def test_reads_a_run(self, tmp_path):
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        report = ReportStore(_index_path(tmp_path)).get_trade_history('20260615_120000_aaaaaaaa')
        assert report is not None
        assert report.count == 3

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get_trade_history('does_not_exist') is None

    def test_resolves_autotrader_run(self, tmp_path):
        _write_run(tmp_path, RUN_TYPE_LIVE, 'my_profile', '20260615_130000_bbbbbbbb')
        report = ReportStore(_index_path(tmp_path)).get_trade_history('20260615_130000_bbbbbbbb')
        assert report is not None and report.count == 3


class TestFilter:
    def test_filter_by_symbol(self, tmp_path):
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        report = ReportStore(_index_path(tmp_path)).get_trade_history('20260615_120000_aaaaaaaa', symbol='GBPUSD')
        assert report.count == 1 and report.trades[0].position_id == 'p2'

    def test_filter_by_close_reason(self, tmp_path):
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        report = ReportStore(_index_path(tmp_path)).get_trade_history(
            '20260615_120000_aaaaaaaa', close_reason='sl_triggered')
        assert {r.position_id for r in report.trades} == {'p2', 'p3'}


class TestListRuns:
    def test_lists_both_groups_newest_first(self, tmp_path):
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        _write_run(tmp_path, RUN_TYPE_LIVE, 'my_profile', '20260615_130000_bbbbbbbb')
        assert [run.run_id for run in ReportStore(_index_path(tmp_path)).list_runs()] == [
            '20260615_130000_bbbbbbbb', '20260615_120000_aaaaaaaa']

    def test_carries_group_and_owner_name(self, tmp_path):
        """The listing is the viewer's run picker — id alone cannot tell sim from live."""
        _write_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        _write_run(tmp_path, RUN_TYPE_LIVE, 'my_profile', '20260615_130000_bbbbbbbb')
        runs = {run.run_id: run for run in ReportStore(_index_path(tmp_path)).list_runs()}
        assert (runs['20260615_120000_aaaaaaaa'].group, runs['20260615_120000_aaaaaaaa'].name) == (
            RUN_TYPE_SIMULATION, 'my_set')
        assert (runs['20260615_130000_bbbbbbbb'].group, runs['20260615_130000_bbbbbbbb'].name) == (
            RUN_TYPE_LIVE, 'my_profile')

    def test_empty_logs_tree_lists_nothing(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).list_runs() == []


class TestCsv:
    """CSV mirror of the model — same columns as JSON / API."""

    def test_csv_header_and_rows(self, tmp_path):
        write_trade_history_csv(_report(), tmp_path)
        lines = (tmp_path / 'trade_history.csv').read_text().splitlines()
        # run_id leads every row: a CSV gets exported and merged, and a row that does not
        # name its run is a row nobody can trace back (#475).
        assert lines[0].startswith('run_id,position_id,symbol,direction')
        assert len(lines) == 1 + 3                 # header + 3 rows
        assert all(line.startswith(_RUN_ID + ',') for line in lines[1:])
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
    return OrderHistoryReport(run_id=_RUN_ID, orders=rows, count=len(rows), symbols=['EURUSD', 'GBPUSD'])


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
    return PortfolioReport(run_id=_RUN_ID, units=[unit], aggregates=[agg])


class TestOrderHistory:
    def test_reads_and_filters(self, tmp_path):
        run_dir = _planted_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        write_artifact(_order_report(), run_dir / IO_SUBDIR, ORDER_HISTORY_ARTIFACT)

        full = ReportStore(_index_path(tmp_path)).get_order_history('20260615_120000_aaaaaaaa')
        assert full.count == 2

        rejected = ReportStore(_index_path(tmp_path)).get_order_history(
            '20260615_120000_aaaaaaaa', status='rejected')
        assert rejected.count == 1 and rejected.orders[0].order_id == 'o2'

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get_order_history('nope') is None


class TestPortfolio:
    def test_reads_portfolio(self, tmp_path):
        run_dir = _planted_run(tmp_path, RUN_TYPE_LIVE, 'my_profile', '20260615_130000_bbbbbbbb')
        write_artifact(_portfolio_report(), run_dir / IO_SUBDIR, PORTFOLIO_ARTIFACT)

        report = ReportStore(_index_path(tmp_path)).get('20260615_130000_bbbbbbbb', PORTFOLIO_ARTIFACT)
        assert report is not None
        assert len(report.units) == 1 and report.units[0].net_profit == 60.0
        assert report.aggregates[0].currency == 'USD'

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get('nope', PORTFOLIO_ARTIFACT) is None


def _execution_stats_report() -> ExecutionStatsReport:
    unit = ExecutionStatsRow(
        name='s1', symbol='EURUSD', orders_sent=5, orders_executed=4,
        orders_rejected=1, sl_tp_triggered=2)
    totals = ExecutionStatsTotals(
        orders_sent=5, orders_executed=4, orders_rejected=1, sl_tp_triggered=2)
    return ExecutionStatsReport(run_id=_RUN_ID, units=[unit], totals=totals)


class TestExecutionStats:
    def test_reads_execution_stats(self, tmp_path):
        run_dir = _planted_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        write_artifact(_execution_stats_report(), run_dir / IO_SUBDIR, EXECUTION_STATS_ARTIFACT)

        report = ReportStore(_index_path(tmp_path)).get('20260615_120000_aaaaaaaa', EXECUTION_STATS_ARTIFACT)
        assert report is not None
        assert report.units[0].sl_tp_triggered == 2
        assert report.totals.orders_executed == 4

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get('nope', EXECUTION_STATS_ARTIFACT) is None

    def test_csv_header_and_rows(self, tmp_path):
        write_execution_stats_csv(_execution_stats_report(), tmp_path)
        lines = (tmp_path / 'execution_stats.csv').read_text().splitlines()
        assert lines[0].startswith('run_id,name,symbol,orders_sent')
        assert len(lines) == 1 + 1                 # header + 1 unit row
        assert lines[1].startswith(_RUN_ID + ',')
        assert 'EURUSD' in lines[1]


def _pending_orders_report() -> PendingOrdersReport:
    unit = PendingOrdersUnitRow(
        name='s1', symbol='EURUSD', total_resolved=3, total_filled=2,
        total_force_closed=1, avg_latency_ms=42.0, min_latency_ms=21.0, max_latency_ms=60.0,
        active_limit_orders=[ActiveOrderRow(
            order_id='L1', order_type='limit', direction='long', lots=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.11)])
    return PendingOrdersReport(run_id=_RUN_ID, units=[unit])


class TestPendingOrders:
    def test_reads_pending_orders(self, tmp_path):
        run_dir = _planted_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        write_artifact(_pending_orders_report(), run_dir / IO_SUBDIR, PENDING_ORDERS_ARTIFACT)

        report = ReportStore(_index_path(tmp_path)).get('20260615_120000_aaaaaaaa', PENDING_ORDERS_ARTIFACT)
        assert report is not None
        u = report.units[0]
        assert u.total_resolved == 3 and u.avg_latency_ms == 42.0
        assert u.active_limit_orders[0].order_id == 'L1'

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get('nope', PENDING_ORDERS_ARTIFACT) is None


def _scenario_details_report() -> ScenarioDetailsReport:
    return ScenarioDetailsReport(run_id=_RUN_ID, units=[
        ScenarioDetailsRow(
            name='s1', symbol='EURUSD', data_source='mt5', status='success',
            ticks_processed=15000, buy_signals=296, sell_signals=263, worker_count=2),
        ScenarioDetailsRow(
            name='bad', symbol='BTCUSD', data_source='kraken_spot', status='failed',
            error_type='ValidationError', error_message='start before data'),
    ])


class TestScenarioDetails:
    def test_reads_scenario_details(self, tmp_path):
        run_dir = _planted_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        write_artifact(_scenario_details_report(), run_dir / IO_SUBDIR, SCENARIO_DETAILS_ARTIFACT)

        report = ReportStore(_index_path(tmp_path)).get('20260615_120000_aaaaaaaa', SCENARIO_DETAILS_ARTIFACT)
        assert report is not None
        assert [u.status for u in report.units] == ['success', 'failed']
        assert report.units[0].buy_signals == 296

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get('nope', SCENARIO_DETAILS_ARTIFACT) is None


def _run_summary() -> RunSummary:
    return RunSummary(run_id=_RUN_ID, 
        currencies=[RunSummaryCurrency(
            currency='USD', net_pnl=60.0, profit_factor=2.5, win_rate=0.6, max_drawdown=12.0,
            total_fees=5.0, total_trades=10, winning_trades=6, losing_trades=4,
            expectancy=0.5, avg_win_r=2.0, avg_loss_r=-1.0, r_trade_count=4)],
        orders_sent=5, orders_executed=4, orders_rejected=1, sl_tp_triggered=2, unit_count=1)


class TestRunSummary:
    def test_reads_run_summary(self, tmp_path):
        run_dir = _planted_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        write_artifact(_run_summary(), run_dir / IO_SUBDIR, RUN_SUMMARY_ARTIFACT)

        rs = ReportStore(_index_path(tmp_path)).get('20260615_120000_aaaaaaaa', RUN_SUMMARY_ARTIFACT)
        assert rs is not None
        assert rs.currencies[0].expectancy == 0.5
        assert rs.orders_executed == 4 and rs.unit_count == 1

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get('nope', RUN_SUMMARY_ARTIFACT) is None


def _broker_report() -> BrokerReport:
    return BrokerReport(run_id=_RUN_ID, units=[BrokerInfoRow(
        broker_type='kraken_spot', market_type='crypto', company='Kraken',
        config_hash='abcd1234', scenarios=['btc_run'],
        symbols=[BrokerSymbolRow(symbol='BTCUSD', base_currency='BTC', quote_currency='USD')])])


def _warnings_errors_report() -> WarningsErrorsReport:
    return WarningsErrorsReport(run_id=_RUN_ID, 
        warnings=[WarningRow(tier='major', scope='run', message='DEBUG MODE')],
        errors=[UnitErrorRow(name='bad', symbol='BTCUSD', error_type='ValidationError')],
        outcome=WarningsErrorsOutcome(failed_count=1, total_units=2))


class TestWarningsErrors:
    def test_reads_warnings_errors(self, tmp_path):
        run_dir = _planted_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        write_artifact(_warnings_errors_report(), run_dir / IO_SUBDIR, WARNINGS_ERRORS_ARTIFACT)

        report = ReportStore(_index_path(tmp_path)).get('20260615_120000_aaaaaaaa', WARNINGS_ERRORS_ARTIFACT)
        assert report is not None
        assert report.warnings[0].message == 'DEBUG MODE'
        assert report.errors[0].name == 'bad'
        assert report.outcome.failed_count == 1

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get('nope', WARNINGS_ERRORS_ARTIFACT) is None


def _aggregated_portfolio_report() -> AggregatedPortfolioReport:
    headline = PortfolioAggregateRow(
        currency='USD', unit_count=2, total_trades=4, winning_trades=2, losing_trades=2,
        win_rate=0.5, profit_factor=2.0, total_profit=100.0, total_loss=50.0, net_profit=50.0,
        max_drawdown=12.0, total_fees=5.0)
    row = AggregatedPortfolioRow(headline=headline, initial_balance=2000.0, final_balance=2050.0)
    return AggregatedPortfolioReport(run_id=_RUN_ID, currencies=[AggregatedPortfolioCurrency(
        currency='USD', scenario_count=2, scenario_names=['s1', 's2'], combined=row)])


class TestAggregatedPortfolio:
    def test_reads_aggregated_portfolio(self, tmp_path):
        run_dir = _planted_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        write_artifact(_aggregated_portfolio_report(), run_dir / IO_SUBDIR, AGGREGATED_PORTFOLIO_ARTIFACT)

        report = ReportStore(_index_path(tmp_path)).get('20260615_120000_aaaaaaaa', AGGREGATED_PORTFOLIO_ARTIFACT)
        assert report is not None
        cur = report.currencies[0]
        assert cur.currency == 'USD' and cur.combined.headline.total_trades == 4
        assert cur.combined.initial_balance == 2000.0

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get('nope', AGGREGATED_PORTFOLIO_ARTIFACT) is None


class TestBroker:
    def test_reads_broker(self, tmp_path):
        run_dir = _planted_run(tmp_path, RUN_TYPE_SIMULATION, 'my_set', '20260615_120000_aaaaaaaa')
        write_artifact(_broker_report(), run_dir / IO_SUBDIR, BROKER_ARTIFACT)

        report = ReportStore(_index_path(tmp_path)).get('20260615_120000_aaaaaaaa', BROKER_ARTIFACT)
        assert report is not None
        assert report.units[0].broker_type == 'kraken_spot'
        assert report.units[0].symbols[0].symbol == 'BTCUSD'

    def test_not_found_returns_none(self, tmp_path):
        assert ReportStore(_index_path(tmp_path)).get('nope', BROKER_ARTIFACT) is None
