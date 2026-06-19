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
    ExecutionStatsReport, OrderHistoryReport, PendingOrdersReport, PortfolioReport,
    ProfilingReport, RunSummary, ScenarioDetailsReport, TradeHistoryReport, WorkerDecisionReport)

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


@router.get('/reports/runs/{run_id}/pending-orders', response_model=PendingOrdersReport)
def get_pending_orders(run_id: str) -> PendingOrdersReport:
    """
    Pending-orders report for a run (per-unit lifecycle + latency + active orders).

    Args:
        run_id: The run-timestamp directory name

    Returns:
        The PendingOrdersReport (404 if the run has no pending-orders artifact)
    """
    report = ReportStore().get_pending_orders(run_id)
    if report is None:
        raise ApiException(
            404, 'run_not_found',
            f"No pending-orders artifact for run '{run_id}'")
    return report


@router.get('/reports/runs/{run_id}/scenario-details', response_model=ScenarioDetailsReport)
def get_scenario_details(run_id: str) -> ScenarioDetailsReport:
    """
    Scenario-details report for a run (per-scenario execution + signal metadata, sim-only).

    Args:
        run_id: The run-timestamp directory name

    Returns:
        The ScenarioDetailsReport (404 if the run has no scenario-details artifact)
    """
    report = ReportStore().get_scenario_details(run_id)
    if report is None:
        raise ApiException(
            404, 'run_not_found',
            f"No scenario-details artifact for run '{run_id}'")
    return report


@router.get('/reports/runs/{run_id}/run-summary', response_model=RunSummary)
def get_run_summary(run_id: str) -> RunSummary:
    """
    Cross-section KPI summary for a run (per-currency KPIs + global order counts).

    Args:
        run_id: The run-timestamp directory name

    Returns:
        The RunSummary (404 if the run has no run-summary artifact)
    """
    report = ReportStore().get_run_summary(run_id)
    if report is None:
        raise ApiException(
            404, 'run_not_found',
            f"No run-summary artifact for run '{run_id}'")
    return report


@router.get('/reports/runs/{run_id}/worker-decision', response_model=WorkerDecisionReport)
def get_worker_decision(run_id: str) -> WorkerDecisionReport:
    """
    Worker/decision report for a run (per-unit worker + decision performance, unified).

    Args:
        run_id: The run-timestamp directory name

    Returns:
        The WorkerDecisionReport (404 if the run has no worker-decision artifact)
    """
    report = ReportStore().get_worker_decision(run_id)
    if report is None:
        raise ApiException(
            404, 'run_not_found',
            f"No worker-decision artifact for run '{run_id}'")
    return report


@router.get('/reports/runs/{run_id}/profiling', response_model=ProfilingReport)
def get_profiling(run_id: str) -> ProfilingReport:
    """
    Profiling report for a run (per-scenario operation timing + inter-tick + clipping + warmup, sim-only).

    Args:
        run_id: The run-timestamp directory name

    Returns:
        The ProfilingReport (404 if the run has no profiling artifact)
    """
    report = ReportStore().get_profiling(run_id)
    if report is None:
        raise ApiException(
            404, 'run_not_found',
            f"No profiling artifact for run '{run_id}'")
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
