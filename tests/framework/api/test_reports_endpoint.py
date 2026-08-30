"""
Reports API Endpoint Tests (#391).

Drives the trade-history endpoint through the FastAPI TestClient against a fixture
run artifact in a temporary logs tree (the inline ReportStore is patched to that
root). Covers the happy path, parameter filtering, the not-found and invalid-input
contracts — no simulation or live run required.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from python.api.api_app import create_app
from python.framework.reporting.io.aggregated_portfolio_report_io import (
    write_aggregated_portfolio_report,
)
from python.framework.reporting.io.broker_report_io import write_broker_report
from python.framework.reporting.io.execution_stats_report_io import write_execution_stats_report
from python.framework.reporting.io.feed_stability_report_io import write_feed_stability_report
from python.framework.reporting.io.order_history_report_io import write_order_history_report
from python.framework.reporting.io.pending_orders_report_io import write_pending_orders_report
from python.framework.reporting.io.portfolio_report_io import write_portfolio_report
from python.framework.reporting.io.run_summary_io import write_run_summary
from python.framework.reporting.io.scenario_details_report_io import write_scenario_details_report
from python.framework.reporting.io.signal_report_io import write_signal_report
from python.framework.reporting.io.trade_history_report_io import write_trade_history_report
from python.framework.reporting.io.warnings_errors_report_io import write_warnings_errors_report
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
    FeedStabilityEpisodeRow,
    FeedStabilityReport,
    FeedStabilitySourceRow,
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
    SignalReport,
    SignalSourceRow,
    SignalUsageRow,
    TradeAnalytics,
    TradeHistoryReport,
    TradeHistoryRow,
    UnitErrorRow,
    WarningRow,
    WarningsErrorsOutcome,
    WarningsErrorsReport,
)
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.log_layout_types import RUN_TYPE_SIMULATION

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

_ZERO_ANALYTICS = TradeAnalytics(
    expectancy=0.0, avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=0,
    avg_mae_winners=0.0, avg_mae_losers=0.0, avg_mfe_losers=0.0)

_RUN = '20260615_120000_aaaaaaaa'
_URL = f'/api/v1/reports/runs/{_RUN}/trade-history'
_ORDER_URL = f'/api/v1/reports/runs/{_RUN}/order-history'
_PORTFOLIO_URL = f'/api/v1/reports/runs/{_RUN}/portfolio'
_EXEC_URL = f'/api/v1/reports/runs/{_RUN}/execution-stats'
_PENDING_URL = f'/api/v1/reports/runs/{_RUN}/pending-orders'
_SCENARIO_URL = f'/api/v1/reports/runs/{_RUN}/scenario-details'
_RUNSUMMARY_URL = f'/api/v1/reports/runs/{_RUN}/run-summary'
_BROKER_URL = f'/api/v1/reports/runs/{_RUN}/broker'
_SIGNAL_URL = f'/api/v1/reports/runs/{_RUN}/signal'
_FEED_STABILITY_URL = f'/api/v1/reports/runs/{_RUN}/feed-stability'
_WARNINGS_URL = f'/api/v1/reports/runs/{_RUN}/warnings-errors'
_AGG_URL = f'/api/v1/reports/runs/{_RUN}/aggregated-portfolio'


def _report() -> TradeHistoryReport:
    rows = [
        TradeHistoryRow(
            position_id='p1', symbol='EURUSD', direction='long', lots=0.1,
            entry_price=1.10, entry_time='2025-10-13T08:00:00+00:00',
            exit_price=1.11, exit_time='2025-10-13T08:10:00+00:00', duration_s=600.0,
            close_reason='tp_triggered', gross_pnl=1.0, total_fees=0.2, net_pnl=0.8),
        TradeHistoryRow(
            position_id='p2', symbol='GBPUSD', direction='short', lots=0.1,
            entry_price=1.33, entry_time='2025-10-13T09:00:00+00:00',
            exit_price=1.32, exit_time='2025-10-13T09:10:00+00:00', duration_s=600.0,
            close_reason='sl_triggered', gross_pnl=-1.0, total_fees=0.2, net_pnl=-1.2),
    ]
    return TradeHistoryReport(run_id=_RUN_ID, 
        trades=rows, count=2, symbols=['EURUSD', 'GBPUSD'], analytics=[_ZERO_ANALYTICS])


def _order_report() -> OrderHistoryReport:
    rows = [
        OrderHistoryRow(
            order_id='o1', position_id='p1', symbol='EURUSD', direction='long',
            action='open', status='executed', requested_lots=0.1, executed_lots=0.1,
            executed_price=1.10, execution_time='2025-10-13T08:00:00+00:00',
            commission=0.2, swap=0.0, slippage_points=1.0,
            rejection_reason='', rejection_message=''),
        OrderHistoryRow(
            order_id='o2', position_id='', symbol='GBPUSD', direction='short',
            action='open', status='rejected', requested_lots=0.5, executed_lots=0.0,
            executed_price=0.0, execution_time='', commission=0.0, swap=0.0,
            slippage_points=0.0, rejection_reason='insufficient_margin',
            rejection_message='not enough margin'),
    ]
    return OrderHistoryReport(run_id=_RUN_ID, orders=rows, count=2, symbols=['EURUSD', 'GBPUSD'])


def _portfolio_report() -> PortfolioReport:
    unit = PortfolioUnitRow(
        name='s1', symbol='EURUSD', currency='USD', total_trades=10, winning_trades=6,
        losing_trades=4, win_rate=0.6, profit_factor=2.5, total_profit=100.0,
        total_loss=40.0, net_profit=60.0, max_drawdown=12.0, total_fees=5.0)
    agg = PortfolioAggregateRow(
        currency='USD', unit_count=1, total_trades=10, winning_trades=6, losing_trades=4,
        win_rate=0.6, profit_factor=2.5, total_profit=100.0, total_loss=40.0,
        net_profit=60.0, max_drawdown=12.0, total_fees=5.0)
    return PortfolioReport(run_id=_RUN_ID, units=[unit], aggregates=[agg])


def _execution_stats_report() -> ExecutionStatsReport:
    unit = ExecutionStatsRow(
        name='s1', symbol='EURUSD', orders_sent=5, orders_executed=4,
        orders_rejected=1, sl_tp_triggered=2)
    totals = ExecutionStatsTotals(
        orders_sent=5, orders_executed=4, orders_rejected=1, sl_tp_triggered=2)
    return ExecutionStatsReport(run_id=_RUN_ID, units=[unit], totals=totals)


def _pending_orders_report() -> PendingOrdersReport:
    unit = PendingOrdersUnitRow(
        name='s1', symbol='EURUSD', total_resolved=3, total_filled=2, total_force_closed=1,
        avg_latency_ms=42.0, min_latency_ms=21.0, max_latency_ms=60.0,
        active_limit_orders=[ActiveOrderRow(
            order_id='L1', order_type='limit', direction='long', lots=0.1,
            entry_price=1.10, stop_loss=1.09, take_profit=1.11)])
    return PendingOrdersReport(run_id=_RUN_ID, units=[unit])


def _scenario_details_report() -> ScenarioDetailsReport:
    return ScenarioDetailsReport(run_id=_RUN_ID, units=[
        ScenarioDetailsRow(
            name='s1', symbol='EURUSD', data_source='mt5', status='success',
            ticks_processed=15000, buy_signals=296, worker_count=2),
        ScenarioDetailsRow(
            name='bad', symbol='BTCUSD', status='failed', error_type='ValidationError'),
    ])


def _run_summary() -> RunSummary:
    return RunSummary(run_id=_RUN_ID, 
        currencies=[RunSummaryCurrency(
            currency='USD', net_pnl=60.0, profit_factor=2.5, win_rate=0.6, max_drawdown=12.0,
            total_fees=5.0, total_trades=10, winning_trades=6, losing_trades=4,
            expectancy=0.5, avg_win_r=2.0, avg_loss_r=-1.0, r_trade_count=4)],
        orders_sent=5, orders_executed=4, orders_rejected=1, sl_tp_triggered=2, unit_count=1)


def _broker_report() -> BrokerReport:
    return BrokerReport(run_id=_RUN_ID, units=[BrokerInfoRow(
        broker_type='kraken_spot', market_type='crypto', company='Kraken',
        config_hash='abcd1234', scenarios=['btc_run'],
        symbols=[BrokerSymbolRow(symbol='BTCUSD', base_currency='BTC', quote_currency='USD')])])


def _signal_report() -> SignalReport:
    return SignalReport(run_id=_RUN_ID, units=[SignalSourceRow(
        source='crypto_sentiment_mock', data_origin='synthetic',
        config_fingerprint='mock-1e9e9fc4', cadence_seconds=597.0, snapshot_count=1091,
        trigger_reasons={'scheduled': 1008, 'breaking': 83},
        usages=[SignalUsageRow(
            scenario='btc_run', symbol='BTCUSD', coverage_ratio=1.0,
            fresh_ticks=900, stale_ticks=100, blind_ticks=0, fresh_ratio=0.9)])])


def _feed_stability_report() -> FeedStabilityReport:
    return FeedStabilityReport(run_id=_RUN_ID, 
        units=[FeedStabilitySourceRow(
            source='kraken_spot', domain='tick', stale_seconds=900.0, episode_count=1,
            origins=['stress-injected'], fresh_ticks=900, stale_ticks=100,
            episodes=[FeedStabilityEpisodeRow(
                unit='btc_run', symbol='BTCUSD',
                stale_from='2026-04-30T06:15:00+00:00', stale_to='',
                duration_seconds=900.0, origin='stress-injected', label='w1')])],
        episode_count=1, stale_seconds=900.0, stress_injected_count=1, source_count=1)


def _warnings_errors_report() -> WarningsErrorsReport:
    return WarningsErrorsReport(run_id=_RUN_ID, 
        warnings=[WarningRow(tier='major', scope='run', message='DEBUG MODE')],
        errors=[UnitErrorRow(name='bad', symbol='BTCUSD', error_type='ValidationError')],
        outcome=WarningsErrorsOutcome(failed_count=1, total_units=2))


def _aggregated_portfolio_report() -> AggregatedPortfolioReport:
    headline = PortfolioAggregateRow(
        currency='USD', unit_count=1, total_trades=10, winning_trades=6, losing_trades=4,
        win_rate=0.6, profit_factor=2.5, total_profit=100.0, total_loss=40.0, net_profit=60.0,
        max_drawdown=12.0, total_fees=5.0)
    return AggregatedPortfolioReport(run_id=_RUN_ID, currencies=[AggregatedPortfolioCurrency(
        currency='USD', scenario_count=1, scenario_names=['s1'],
        combined=AggregatedPortfolioRow(headline=headline, initial_balance=1000.0))])



def _run_logs(root: Path) -> RunLogPaths:
    """The two run-type roots under a tmp logs tree."""
    return RunLogPaths(simulation=root / 'simulation', live=root / 'live')


def _index_path(root: Path) -> Path:
    """The tmp tree's OWN index. The store resolves through it, so it has to be the tmp one."""
    return root / 'index.parquet'


def _plant_run(root: Path, run_id: str = _RUN) -> Path:
    """
    Register a run the way a real one registers itself: header first, index row with it.

    Returns:
        The run's io/ directory, created
    """
    run_dir = _run_logs(root).simulation / 'my_set' / run_id
    (run_dir / IO_SUBDIR).mkdir(parents=True)
    RunIndex(_index_path(root)).register_run(
        RunHeader(run_id=run_id, start_time=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
                  run_type=RUN_TYPE_SIMULATION, run_name='my_set'), run_dir)
    return run_dir / IO_SUBDIR

@pytest.fixture
def client(tmp_path: Path):
    # Artifacts live in the run's io/ subfolder (#396 housekeeping)
    io_dir = _plant_run(tmp_path)
    write_trade_history_report(_report(), io_dir)
    write_order_history_report(_order_report(), io_dir)
    write_portfolio_report(_portfolio_report(), io_dir)
    write_execution_stats_report(_execution_stats_report(), io_dir)
    write_pending_orders_report(_pending_orders_report(), io_dir)
    write_scenario_details_report(_scenario_details_report(), io_dir)
    write_run_summary(_run_summary(), io_dir)
    write_broker_report(_broker_report(), io_dir)
    write_signal_report(_signal_report(), io_dir)
    write_feed_stability_report(_feed_stability_report(), io_dir)
    write_warnings_errors_report(_warnings_errors_report(), io_dir)
    write_aggregated_portfolio_report(_aggregated_portfolio_report(), io_dir)
    # The endpoint constructs ReportStore() inline → point it at the fixture logs root
    with patch('python.api.endpoints.reports_router.ReportStore', lambda: ReportStore(_index_path(tmp_path))):
        yield TestClient(create_app())


def test_returns_full_report(client):
    response = client.get(_URL)
    assert response.status_code == 200
    body = response.json()
    assert body['count'] == 2
    assert body['symbols'] == ['EURUSD', 'GBPUSD']


def test_filter_by_symbol(client):
    response = client.get(_URL, params={'symbol': 'GBPUSD'})
    assert response.status_code == 200
    assert response.json()['count'] == 1
    assert response.json()['trades'][0]['position_id'] == 'p2'


def test_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/trade-history')
    assert response.status_code == 404


def test_invalid_timestamp(client):
    response = client.get(_URL, params={'start': 'not-a-date'})
    assert response.status_code == 400


def test_order_history_returns_full(client):
    response = client.get(_ORDER_URL)
    assert response.status_code == 200
    assert response.json()['count'] == 2


def test_order_history_filter_by_status(client):
    response = client.get(_ORDER_URL, params={'status': 'rejected'})
    assert response.status_code == 200
    assert response.json()['count'] == 1
    assert response.json()['orders'][0]['order_id'] == 'o2'


def test_order_history_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/order-history')
    assert response.status_code == 404


def test_portfolio_returns(client):
    response = client.get(_PORTFOLIO_URL)
    assert response.status_code == 200
    body = response.json()
    assert len(body['units']) == 1
    assert body['units'][0]['net_profit'] == 60.0
    assert body['aggregates'][0]['currency'] == 'USD'


def test_portfolio_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/portfolio')
    assert response.status_code == 404


def test_execution_stats_returns(client):
    response = client.get(_EXEC_URL)
    assert response.status_code == 200
    body = response.json()
    assert len(body['units']) == 1
    assert body['units'][0]['sl_tp_triggered'] == 2
    assert body['totals']['orders_executed'] == 4


def test_execution_stats_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/execution-stats')
    assert response.status_code == 404


def test_pending_orders_returns(client):
    response = client.get(_PENDING_URL)
    assert response.status_code == 200
    body = response.json()
    assert len(body['units']) == 1
    assert body['units'][0]['total_resolved'] == 3
    assert body['units'][0]['active_limit_orders'][0]['order_id'] == 'L1'


def test_pending_orders_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/pending-orders')
    assert response.status_code == 404


def test_scenario_details_returns(client):
    response = client.get(_SCENARIO_URL)
    assert response.status_code == 200
    body = response.json()
    assert [u['status'] for u in body['units']] == ['success', 'failed']
    assert body['units'][0]['buy_signals'] == 296


def test_scenario_details_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/scenario-details')
    assert response.status_code == 404


def test_run_summary_returns(client):
    response = client.get(_RUNSUMMARY_URL)
    assert response.status_code == 200
    body = response.json()
    assert body['currencies'][0]['currency'] == 'USD'
    assert body['currencies'][0]['expectancy'] == 0.5
    assert body['orders_executed'] == 4 and body['unit_count'] == 1


def test_run_summary_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/run-summary')
    assert response.status_code == 404


def test_broker_returns(client):
    response = client.get(_BROKER_URL)
    assert response.status_code == 200
    body = response.json()
    assert body['units'][0]['broker_type'] == 'kraken_spot'
    assert body['units'][0]['symbols'][0]['symbol'] == 'BTCUSD'


def test_broker_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/broker')
    assert response.status_code == 404


def test_signal_returns(client):
    response = client.get(_SIGNAL_URL)
    assert response.status_code == 200
    body = response.json()
    assert body['units'][0]['source'] == 'crypto_sentiment_mock'
    assert body['units'][0]['usages'][0]['stale_ticks'] == 100


def test_signal_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/signal')
    assert response.status_code == 404


def test_feed_stability_returns(client):
    response = client.get(_FEED_STABILITY_URL)
    assert response.status_code == 200
    body = response.json()
    assert body['units'][0]['source'] == 'kraken_spot'
    assert body['units'][0]['episodes'][0]['origin'] == 'stress-injected'
    assert body['episode_count'] == 1


def test_feed_stability_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/feed-stability')
    assert response.status_code == 404


def test_warnings_errors_returns(client):
    response = client.get(_WARNINGS_URL)
    assert response.status_code == 200
    body = response.json()
    assert body['warnings'][0]['message'] == 'DEBUG MODE'
    assert body['errors'][0]['name'] == 'bad'
    assert body['outcome']['failed_count'] == 1


def test_warnings_errors_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/warnings-errors')
    assert response.status_code == 404


def test_warnings_errors_from_an_older_schema_is_named_not_a_server_error(tmp_path):
    """
    `logged_errors` used to be a list of bare strings. An artifact written back then no longer
    validates, and a raw ValidationError would escape as an unexplained 500 for every run that
    predates the change. §27 rules out a compatibility layer, so the read path names the
    condition instead — an artifact this old is regenerated by re-running, not repaired.
    """
    io_dir = _plant_run(tmp_path)
    (io_dir / 'warnings_errors.json').write_text(
        '{"warnings": [], "errors": [{"name": "bad", "logged_errors": ["e1"]}], "outcome": {}}',
        encoding='utf-8')

    with patch('python.api.endpoints.reports_router.ReportStore',
               lambda: ReportStore(_index_path(tmp_path))):
        response = TestClient(create_app()).get(_WARNINGS_URL)

    assert response.status_code == 409
    assert response.json()['error'] == 'artifact_unreadable'
    assert 'older schema' in response.json()['detail']


def test_aggregated_portfolio_returns(client):
    response = client.get(_AGG_URL)
    assert response.status_code == 200
    body = response.json()
    cur = body['currencies'][0]
    assert cur['currency'] == 'USD'
    assert cur['combined']['headline']['total_trades'] == 10
    assert cur['combined']['initial_balance'] == 1000.0


def test_aggregated_portfolio_run_not_found(client):
    response = client.get('/api/v1/reports/runs/nope/aggregated-portfolio')
    assert response.status_code == 404
