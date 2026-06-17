"""
Reports API router (#391) — read-only access to persisted run reports.

The first consumer of the unified reporting model: serves a run's trade-history
report (the same canonical model the console + CSV render), with parameter
filtering pre-applied so the frontend renders, not derives.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from python.framework.exceptions.api_errors import ApiException
from python.framework.reporting.run_reports.report_store import ReportStore
from python.framework.types.api.report_types import (
    ExecutionStatsReport, OrderHistoryReport, PortfolioReport, TradeHistoryReport)

router = APIRouter()


@router.get('/reports/runs/{run_id}/trade-history', response_model=TradeHistoryReport)
def get_trade_history(
    run_id: str,
    symbol: Optional[str] = Query(None, description='Filter by symbol'),
    close_reason: Optional[str] = Query(
        None, description="Filter by close reason ('sl_triggered', 'tp_triggered', ...)"),
    start: Optional[str] = Query(None, description='ISO-8601 UTC; entry_time lower bound'),
    end: Optional[str] = Query(None, description='ISO-8601 UTC; entry_time upper bound'),
) -> TradeHistoryReport:
    """
    Trade-history report for a run, filtered by the query parameters.

    Args:
        run_id: The run-timestamp directory name
        symbol / close_reason / start / end: Optional filters

    Returns:
        The filtered TradeHistoryReport (404 if the run has no trade-history artifact)
    """
    report = ReportStore().get_trade_history(
        run_id,
        symbol=symbol,
        close_reason=close_reason,
        start=_parse_iso(start, 'start'),
        end=_parse_iso(end, 'end'),
    )
    if report is None:
        raise ApiException(
            404, 'run_not_found',
            f"No trade-history artifact for run '{run_id}'")
    return report


@router.get('/reports/runs/{run_id}/order-history', response_model=OrderHistoryReport)
def get_order_history(
    run_id: str,
    symbol: Optional[str] = Query(None, description='Filter by symbol'),
    status: Optional[str] = Query(
        None, description="Filter by order status ('executed', 'rejected', ...)"),
) -> OrderHistoryReport:
    """
    Order-history report for a run, filtered by the query parameters.

    Args:
        run_id: The run-timestamp directory name
        symbol / status: Optional filters

    Returns:
        The filtered OrderHistoryReport (404 if the run has no order-history artifact)
    """
    report = ReportStore().get_order_history(run_id, symbol=symbol, status=status)
    if report is None:
        raise ApiException(
            404, 'run_not_found',
            f"No order-history artifact for run '{run_id}'")
    return report


@router.get('/reports/runs/{run_id}/portfolio', response_model=PortfolioReport)
def get_portfolio(run_id: str) -> PortfolioReport:
    """
    Portfolio headline report for a run (per-unit rows + per-currency aggregates).

    Args:
        run_id: The run-timestamp directory name

    Returns:
        The PortfolioReport (404 if the run has no portfolio artifact)
    """
    report = ReportStore().get_portfolio(run_id)
    if report is None:
        raise ApiException(
            404, 'run_not_found',
            f"No portfolio artifact for run '{run_id}'")
    return report


@router.get('/reports/runs/{run_id}/execution-stats', response_model=ExecutionStatsReport)
def get_execution_stats(run_id: str) -> ExecutionStatsReport:
    """
    Execution-stats report for a run (per-unit order counts + summed totals).

    Args:
        run_id: The run-timestamp directory name

    Returns:
        The ExecutionStatsReport (404 if the run has no execution-stats artifact)
    """
    report = ReportStore().get_execution_stats(run_id)
    if report is None:
        raise ApiException(
            404, 'run_not_found',
            f"No execution-stats artifact for run '{run_id}'")
    return report


def _parse_iso(value: Optional[str], field: str) -> Optional[datetime]:
    """Parse an ISO-8601 query param, or raise a 400 ApiException."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ApiException(
            400, 'invalid_timestamp', f"'{field}' must be ISO-8601, got '{value}'")
