"""
Reports API Endpoint Tests (#391).

Drives the trade-history endpoint through the FastAPI TestClient against a fixture
run artifact in a temporary logs tree (the inline ReportStore is patched to that
root). Covers the happy path, parameter filtering, the not-found and invalid-input
contracts — no simulation or live run required.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from python.api.api_app import create_app
from python.framework.reporting.run_reports.execution_stats_report_io import write_execution_stats_report
from python.framework.reporting.run_reports.order_history_report_io import write_order_history_report
from python.framework.reporting.run_reports.portfolio_report_io import write_portfolio_report
from python.framework.reporting.run_reports.report_store import ReportStore
from python.framework.reporting.run_reports.trade_history_report_io import write_trade_history_report
from python.framework.types.api.report_types import (
    ExecutionStatsReport, ExecutionStatsRow, ExecutionStatsTotals,
    OrderHistoryReport, OrderHistoryRow, PortfolioAggregateRow, PortfolioReport,
    PortfolioUnitRow, TradeAnalytics, TradeHistoryReport, TradeHistoryRow)

_ZERO_ANALYTICS = TradeAnalytics(
    expectancy=0.0, avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=0,
    avg_mae_winners=0.0, avg_mae_losers=0.0, avg_mfe_losers=0.0)

_RUN = '20260615_120000'
_URL = f'/api/v1/reports/runs/{_RUN}/trade-history'
_ORDER_URL = f'/api/v1/reports/runs/{_RUN}/order-history'
_PORTFOLIO_URL = f'/api/v1/reports/runs/{_RUN}/portfolio'
_EXEC_URL = f'/api/v1/reports/runs/{_RUN}/execution-stats'


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
    return TradeHistoryReport(
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
    return OrderHistoryReport(orders=rows, count=2, symbols=['EURUSD', 'GBPUSD'])


def _portfolio_report() -> PortfolioReport:
    unit = PortfolioUnitRow(
        name='s1', symbol='EURUSD', currency='USD', total_trades=10, winning_trades=6,
        losing_trades=4, win_rate=0.6, profit_factor=2.5, total_profit=100.0,
        total_loss=40.0, net_profit=60.0, max_drawdown=12.0, total_fees=5.0)
    agg = PortfolioAggregateRow(
        currency='USD', unit_count=1, total_trades=10, winning_trades=6, losing_trades=4,
        win_rate=0.6, profit_factor=2.5, total_profit=100.0, total_loss=40.0,
        net_profit=60.0, max_drawdown=12.0, total_fees=5.0)
    return PortfolioReport(units=[unit], aggregates=[agg])


def _execution_stats_report() -> ExecutionStatsReport:
    unit = ExecutionStatsRow(
        name='s1', symbol='EURUSD', orders_sent=5, orders_executed=4,
        orders_rejected=1, sl_tp_triggered=2)
    totals = ExecutionStatsTotals(
        orders_sent=5, orders_executed=4, orders_rejected=1, sl_tp_triggered=2)
    return ExecutionStatsReport(units=[unit], totals=totals)


@pytest.fixture
def client(tmp_path: Path):
    run_dir = tmp_path / 'scenario_sets' / 'my_set' / _RUN
    run_dir.mkdir(parents=True)
    write_trade_history_report(_report(), run_dir)
    write_order_history_report(_order_report(), run_dir)
    write_portfolio_report(_portfolio_report(), run_dir)
    write_execution_stats_report(_execution_stats_report(), run_dir)
    # The endpoint constructs ReportStore() inline → point it at the fixture logs root
    with patch('python.api.endpoints.reports_router.ReportStore', lambda: ReportStore(tmp_path)):
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
