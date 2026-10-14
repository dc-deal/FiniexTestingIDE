"""
Reports API router (#391) — read-only access to persisted run reports.

The first consumer of the unified reporting model: serves a run's trade-history
report (the same canonical model the console + CSV render), with parameter
filtering pre-applied so the frontend renders, not derives.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from python.api.api_error_catalog import (
    ARTIFACT_NOT_PRODUCED,
    ARTIFACT_UNREADABLE,
    CONFIG_SNAPSHOT_MISSING,
    INVALID_TIMESTAMP,
    REPORTS_NOT_COMMISSIONED,
    RUN_HEADER_MISSING,
    RUN_NOT_COMPLETED,
    RUN_NOT_FOUND,
    api_error,
)
from python.api.api_route_documents import describes
from python.framework.exceptions.api_errors import ApiException
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.artifact_specs import (
    AGGREGATED_PORTFOLIO_ARTIFACT,
    BOOKING_PERIODS_ARTIFACT,
    BROKER_ARTIFACT,
    EXECUTION_STATS_ARTIFACT,
    FEED_STABILITY_ARTIFACT,
    PENDING_ORDERS_ARTIFACT,
    PORTFOLIO_ARTIFACT,
    PROFILING_ARTIFACT,
    RUN_SUMMARY_ARTIFACT,
    SCENARIO_DETAILS_ARTIFACT,
    SIGNAL_ARTIFACT,
    VENUE_ACCOUNT_ARTIFACT,
    WARNINGS_ERRORS_ARTIFACT,
    WORKER_DECISION_ARTIFACT,
)
from python.framework.reporting.store.report_store import ReportStore
from python.framework.types.api.report_types import (
    AggregatedPortfolioReport,
    BookingPeriodsReport,
    BrokerReport,
    ExecutionStatsReport,
    FeedStabilityReport,
    OrderEventsReport,
    OrderHistoryReport,
    PendingOrdersReport,
    PortfolioReport,
    ProfilingReport,
    RunConfigSnapshot,
    RunHeader,
    RunInfo,
    RunListResponse,
    RunReporting,
    RunSummary,
    ScenarioDetailsReport,
    SignalReport,
    TradeHistoryReport,
    VenueAccountReport,
    WarningsErrorsReport,
    WorkerDecisionReport,
)

router = APIRouter()


def _missing_artifact(run_id: str, section: str) -> ApiException:
    """
    The 404 for a report section a run does not have — naming WHY, never only "not found".

    One absence has four causes, and a consumer renders each differently: the run is unknown; it
    was started without reports; it has produced none YET — still running, or it ended before
    its report phase, which look the same from here (§44); or it produced others but not this
    one, because its pipeline does not write the section or its outcome left nothing to write.
    All four are read from the run's index row, which already records `reporting` and the
    artifacts the run persisted — no directory is walked to answer.

    Args:
        run_id: The run asked for
        section: The report section's route name

    Returns:
        The 404 to raise, with one error code per cause
    """
    run = next((info for info in ReportStore().list_runs() if info.run_id == run_id), None)
    if run is None:
        return api_error(RUN_NOT_FOUND, run_id=run_id)
    if run.reporting == RunReporting.NONE:
        return api_error(REPORTS_NOT_COMMISSIONED, run_id=run_id)
    if not run.artifacts:
        return api_error(RUN_NOT_COMPLETED, run_id=run_id)
    return api_error(ARTIFACT_NOT_PRODUCED, run_id=run_id,
                     artifact_count=len(run.artifacts), section=section)


@router.get('/reports/runs', response_model=RunListResponse,
            openapi_extra=describes('runs'))
def list_runs() -> RunListResponse:
    """
    Every indexed run, newest first, each with what the run-results ledger recorded it did.

    Every run is here, whether or not it produced a report: a run with no artifacts exists as
    logs only, and `artifacts` being empty beside `reporting` is what separates a run that died
    before its report phase from one that was never commissioned to write any.

    Returns:
        The RunListResponse (empty list when nothing has been indexed yet)
    """
    runs: list[RunInfo] = ReportStore().list_runs_with_results()
    return RunListResponse(runs=runs, count=len(runs))


@router.get('/reports/runs/{run_id}/trade-history', response_model=TradeHistoryReport,
            openapi_extra=describes('trade-history'))
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
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index
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
        raise _missing_artifact(run_id, 'trade-history')
    return report


@router.get('/reports/runs/{run_id}/order-history', response_model=OrderHistoryReport,
            openapi_extra=describes('order-history'))
def get_order_history(
    run_id: str,
    symbol: Optional[str] = Query(None, description='Filter by symbol'),
    status: Optional[str] = Query(
        None, description="Filter by order status ('executed', 'rejected', ...)"),
) -> OrderHistoryReport:
    """
    Order-history report for a run, filtered by the query parameters.

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index
        symbol / status: Optional filters

    Returns:
        The filtered OrderHistoryReport (404 if the run has no order-history artifact)
    """
    report = ReportStore().get_order_history(run_id, symbol=symbol, status=status)
    if report is None:
        raise _missing_artifact(run_id, 'order-history')
    return report


@router.get('/reports/runs/{run_id}/order-events', response_model=OrderEventsReport,
            openapi_extra=describes('order-events'))
def get_order_events(
    run_id: str,
    scenario_name: Optional[str] = Query(None, description='Keep only this unit'),
    order_id: Optional[str] = Query(
        None, description="Keep only events with this order id — a position's open and its "
                          'market closes share one; a protective order has its own and names '
                          'the position in position_id'),
) -> OrderEventsReport:
    """
    The run's order-event stream — every order transition, optionally narrowed (#362).

    Served while a run is still going: a live session writes its stream from its first order.

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index
        scenario_name / order_id: Optional filters

    Returns:
        The OrderEventsReport (404 if the run has no stream, 409 if it cannot be read)
    """
    try:
        report = ReportStore().get_order_events(
            run_id, scenario_name=scenario_name, order_id=order_id)
    except ReportArtifactUnreadableError as e:
        raise api_error(ARTIFACT_UNREADABLE, reason=str(e)) from e
    if report is None:
        raise _missing_artifact(run_id, 'order-events')
    return report


@router.get('/reports/runs/{run_id}/portfolio', response_model=PortfolioReport,
            openapi_extra=describes('portfolio'))
def get_portfolio(run_id: str) -> PortfolioReport:
    """
    Portfolio headline report for a run (per-unit rows + per-currency aggregates).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The PortfolioReport (404 if the run has no portfolio artifact)
    """
    report = ReportStore().get(run_id, PORTFOLIO_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'portfolio')
    return report


@router.get('/reports/runs/{run_id}/execution-stats', response_model=ExecutionStatsReport,
            openapi_extra=describes('execution-stats'))
def get_execution_stats(run_id: str) -> ExecutionStatsReport:
    """
    Execution-stats report for a run (per-unit order counts + summed totals).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The ExecutionStatsReport (404 if the run has no execution-stats artifact)
    """
    report = ReportStore().get(run_id, EXECUTION_STATS_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'execution-stats')
    return report


@router.get('/reports/runs/{run_id}/pending-orders', response_model=PendingOrdersReport,
            openapi_extra=describes('pending-orders'))
def get_pending_orders(run_id: str) -> PendingOrdersReport:
    """
    Pending-orders report for a run (per-unit lifecycle + latency + active orders).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The PendingOrdersReport (404 if the run has no pending-orders artifact)
    """
    report = ReportStore().get(run_id, PENDING_ORDERS_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'pending-orders')
    return report


@router.get('/reports/runs/{run_id}/scenario-details', response_model=ScenarioDetailsReport,
            openapi_extra=describes('scenario-details'))
def get_scenario_details(run_id: str) -> ScenarioDetailsReport:
    """
    Scenario-details report for a run (per-scenario execution + signal metadata, sim-only).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The ScenarioDetailsReport (404 if the run has no scenario-details artifact)
    """
    report = ReportStore().get(run_id, SCENARIO_DETAILS_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'scenario-details')
    return report


@router.get('/reports/runs/{run_id}/run-summary', response_model=RunSummary,
            openapi_extra=describes('run-summary'))
def get_run_summary(run_id: str) -> RunSummary:
    """
    Cross-section KPI summary for a run (per-currency KPIs + global order counts).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The RunSummary (404 if the run has no run-summary artifact)
    """
    report = ReportStore().get(run_id, RUN_SUMMARY_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'run-summary')
    return report


@router.get('/reports/runs/{run_id}/worker-decision', response_model=WorkerDecisionReport,
            openapi_extra=describes('worker-decision'))
def get_worker_decision(run_id: str) -> WorkerDecisionReport:
    """
    Worker/decision report for a run (per-unit worker + decision performance, unified).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The WorkerDecisionReport (404 if the run has no worker-decision artifact)
    """
    report = ReportStore().get(run_id, WORKER_DECISION_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'worker-decision')
    return report


@router.get('/reports/runs/{run_id}/profiling', response_model=ProfilingReport,
            openapi_extra=describes('profiling'))
def get_profiling(run_id: str) -> ProfilingReport:
    """
    Profiling report for a run (per-scenario operation timing + inter-tick + clipping + warmup, sim-only).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The ProfilingReport (404 if the run has no profiling artifact)
    """
    report = ReportStore().get(run_id, PROFILING_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'profiling')
    return report


@router.get('/reports/runs/{run_id}/aggregated-portfolio', response_model=AggregatedPortfolioReport,
            openapi_extra=describes('aggregated-portfolio'))
def get_aggregated_portfolio(run_id: str) -> AggregatedPortfolioReport:
    """
    Aggregated per-currency portfolio report for a run (the rich detail view, sim).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The AggregatedPortfolioReport (404 if the run has no aggregated-portfolio artifact)
    """
    report = ReportStore().get(run_id, AGGREGATED_PORTFOLIO_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'aggregated-portfolio')
    return report


@router.get('/reports/runs/{run_id}/warnings-errors', response_model=WarningsErrorsReport,
            openapi_extra=describes('warnings-errors'))
def get_warnings_errors(run_id: str) -> WarningsErrorsReport:
    """
    Warnings & errors report for a run (tiered warnings + per-unit errors + outcome, both pipelines).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The WarningsErrorsReport (404 if the run has no warnings-errors artifact)
    """
    try:
        report = ReportStore().get(run_id, WARNINGS_ERRORS_ARTIFACT)
    except ReportArtifactUnreadableError as e:
        raise api_error(ARTIFACT_UNREADABLE, reason=str(e)) from e
    if report is None:
        raise _missing_artifact(run_id, 'warnings-errors')
    return report


@router.get('/reports/runs/{run_id}/broker', response_model=BrokerReport,
            openapi_extra=describes('broker'))
def get_broker(run_id: str) -> BrokerReport:
    """
    Broker-configuration report for a run (per-broker spec + scenarios + symbols, sim-only).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The BrokerReport (404 if the run has no broker artifact)
    """
    report = ReportStore().get(run_id, BROKER_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'broker')
    return report


@router.get('/reports/runs/{run_id}/signal', response_model=SignalReport,
            openapi_extra=describes('signal'))
def get_signal(run_id: str) -> SignalReport:
    """
    Signal-configuration report for a run (#433): per-source provenance + the run's
    decision basis (fresh / stale / blind ticks per scenario).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The SignalReport (404 if the run has no signal artifact)
    """
    report = ReportStore().get(run_id, SIGNAL_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'signal')
    return report


@router.get('/reports/runs/{run_id}/feed-stability', response_model=FeedStabilityReport,
            openapi_extra=describes('feed-stability'))
def get_feed_stability(run_id: str) -> FeedStabilityReport:
    """
    Feed-stability report for a run (#451): the observed disturbance episodes per source
    across both staleness domains (tick stream + signal sources).

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The FeedStabilityReport (404 if the run has no feed-stability artifact)
    """
    report = ReportStore().get(run_id, FEED_STABILITY_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'feed-stability')
    return report


@router.get('/reports/runs/{run_id}/booking-periods', response_model=BookingPeriodsReport,
            openapi_extra=describes('booking-periods'))
def get_booking_periods(run_id: str) -> BookingPeriodsReport:
    """
    The run's ledger entries (#537): one summary per booking period, and whether they add up.

    Served from the stored artifact rather than rebuilt from the ledger, and the reconciliation
    is the reason. It compares the periods against the figure the run reports by its own
    independent path, and that second figure exists only while the run does — recomputed from
    the ledger the check would be `sum(rows) - sum(rows)`, i.e. a control total that holds by
    construction and can never fail.

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The BookingPeriodsReport (404 if the run has no booking-periods artifact — every run
        from before the artifact existed, which is the same semantics as any other section)
    """
    report = ReportStore().get(run_id, BOOKING_PERIODS_ARTIFACT)
    if report is None:
        raise _missing_artifact(run_id, 'booking-periods')
    return report


@router.get('/reports/runs/{run_id}/venue-account', response_model=VenueAccountReport,
            openapi_extra=describes('venue-account'))
def get_venue_account(run_id: str) -> VenueAccountReport:
    """
    The venue's account per live session (#362): what it held at the start and the end, and
    what the reconciliation recorded in between. AutoTrader only.

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The VenueAccountReport (404 if the run has no venue-account artifact — every backtest,
        and every session that asked its venue nothing; 409 if it cannot be read)
    """
    try:
        report = ReportStore().get(run_id, VENUE_ACCOUNT_ARTIFACT)
    except ReportArtifactUnreadableError as e:
        raise api_error(ARTIFACT_UNREADABLE, reason=str(e)) from e
    if report is None:
        raise _missing_artifact(run_id, 'venue-account')
    return report


def _parse_iso(value: Optional[str], field: str) -> Optional[datetime]:
    """Parse an ISO-8601 query param, or raise a 400 ApiException."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise api_error(INVALID_TIMESTAMP, field=field, value=value)

@router.get('/reports/runs/{run_id}/config', response_model=RunConfigSnapshot,
            openapi_extra=describes('config'))
def get_run_config(run_id: str) -> RunConfigSnapshot:
    """
    The configuration a run was commissioned with.

    The run index already carries two POINTERS — the snapshot's file name and its content id —
    and a reader who sees a change mark between two sessions of a deployment cannot ask what
    changed, because nothing served what they point at. This is that route.

    Two 404s, deliberately distinguished: `run_not_found` when the identity is unknown, and
    `config_snapshot_missing` when the run is known but its configuration cannot be resolved. Both
    pipelines register the configuration BEFORE the header names its id, so the second means a run
    older than the run-config store, or one whose registration failed — never fatal, so that run
    went ahead without a content id. An ordinary state, and reading it as "unknown run" would send
    a consumer looking for the wrong fault.

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The snapshot, parsed, with the name and content id the index attributes to this run
    """
    snapshot = ReportStore().get_config_snapshot(run_id)
    if snapshot is None:
        known = any(r.run_id == run_id for r in ReportStore().list_runs())
        if not known:
            raise api_error(RUN_NOT_FOUND, run_id=run_id)
        raise api_error(CONFIG_SNAPSHOT_MISSING, run_id=run_id)
    return snapshot


@router.get('/reports/runs/{run_id}/header', response_model=RunHeader,
            openapi_extra=describes('run-header'))
def get_run_header(run_id: str) -> RunHeader:
    """
    A run's header: what the run is, who started it and which code ran (#582).

    Written once, at the run's start, so it answers before the run has reported anything. Served
    with every path relative to its repository — the record names paths as the machine saw them,
    and a directory layout does not leave the machine.

    Args:
        run_id: The run's id (<timestamp>_<hash>), resolved through the run index

    Returns:
        The RunHeader (404 if the run is unknown or its header file is missing, 409 if the
        header no longer matches its model)
    """
    try:
        header = ReportStore().get_run_header(run_id)
    except ReportArtifactUnreadableError as e:
        raise api_error(ARTIFACT_UNREADABLE, reason=str(e)) from e
    except FileNotFoundError as e:
        raise api_error(RUN_HEADER_MISSING, run_id=run_id) from e
    if header is None:
        raise api_error(RUN_NOT_FOUND, run_id=run_id)
    return header
