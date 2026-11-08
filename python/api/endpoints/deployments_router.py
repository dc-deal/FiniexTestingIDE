"""
Deployments API router (#539) — read-only access to a live bot's life across its restarts.

A deployment is not a run, it is the sequence of them one bot produced: each restart writes its
own ledger row under its own run id, and #497 gave those rows a common identity so they can be
read back as one history. Until this router the only reader was the CLI, which prints — so the
viewer could show a backtest run and nothing at all about a thirty-day bot.

Which sessions a deployment holds is the run index's answer, and what they booked the
run-results ledger's (#645). The ledger alone was the rule before (operator, 2026-09-22: one
route, one store), and it left a new deployment out of every route until its first sealed
trading day: a session registers in the run index at its start, and books its first ledger row
only at its first seal. A deployment still has no run directory, no header and no artifacts of
its own, so the report store is never asked about one — the run index only says which runs name it.

What a deployment view deliberately does NOT have is a reconciliation line. Over many runs there
is no single run summary to sum against, so the second, independent derivation that makes a
check a check does not exist. It is stated rather than left out: a missing check read as a
passed one is the more expensive mistake.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import APIRouter

from python.api.api_error_catalog import DEPLOYMENT_NOT_FOUND, api_error
from python.api.api_route_documents import describes
from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.builders.deployment_history_builder import (
    compose_deployment_booking_periods,
    compose_deployment_detail,
    compose_deployment_list,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import (
    DeploymentBookingPeriodsResponse,
    DeploymentDetailResponse,
    DeploymentListResponse,
    RunInfo,
)

router = APIRouter()


def _ledger() -> RunResultsLedger:
    """The run-results ledger at its configured location — a deployment's rows live here."""
    return RunResultsLedger(Path(AppConfigManager().get_run_ledger_path()))


def _runs() -> List[RunInfo]:
    """The run index's runs — every session registers there at its start, naming its deployment."""
    file_logging = AppConfigManager().get_file_logging_config_object()
    return RunIndex(file_logging.run_index, file_logging.run_logs).list_runs()


@router.get('/deployments', response_model=DeploymentListResponse,
            openapi_extra=describes('deployments'))
def list_deployments() -> DeploymentListResponse:
    """
    Every deployment, newest first — from its first session's start, not from its first sealed day.

    Returns:
        The DeploymentListResponse — empty when no session has named a continuous deployment
        yet, which is a state and not a failure
    """
    return compose_deployment_list(_ledger().read_rows(), _runs(), datetime.now(timezone.utc))


@router.get('/deployments/{deployment_id}', response_model=DeploymentDetailResponse,
            openapi_extra=describes('deployments'))
def get_deployment(deployment_id: str) -> DeploymentDetailResponse:
    """
    One deployment's sessions, oldest first — a life reads forwards.

    Args:
        deployment_id: The identity the sessions name

    Returns:
        The sessions, the comparability advisory when the configuration moved, the code summary
        when the code did, and how many of this deployment's runs never reached their close (404
        when neither a session in the run index nor a ledger row names this identity)
    """
    # Filtered in the STORE, not after reading: `deployment_id` is the live counterpart of the
    # sweep filter, and its absence is why a live row was written and unreachable (§44).
    rows = _ledger().read_rows(deployment_id=deployment_id)
    runs = [run for run in _runs() if run.parent_id == deployment_id]
    detail = compose_deployment_detail(deployment_id, rows, runs, datetime.now(timezone.utc))
    if detail is None:
        raise api_error(DEPLOYMENT_NOT_FOUND, deployment_id=deployment_id)
    return detail


@router.get('/deployments/{deployment_id}/booking-periods',
            response_model=DeploymentBookingPeriodsResponse,
            openapi_extra=describes('booking-periods'))
def get_deployment_booking_periods(deployment_id: str) -> DeploymentBookingPeriodsResponse:
    """
    Every booking period this deployment booked, across all of its sessions.

    The thirty-day picture in one call: a bar per trading day for a bot that restarted a dozen
    times. The same row shape `/reports/runs/{run_id}/booking-periods` returns, so one renderer
    serves both — and it saves the caller walking the sessions and asking each run in turn.

    Read from the LEDGER, the only store that holds a deployment's periods together. Carries no
    reconciliation, and cannot: across many runs there is no second, independently derived
    figure to check the sum against (see the response model).

    Args:
        deployment_id: The identity the sessions name

    Returns:
        The periods, oldest first within each currency (404 when neither a session in the run
        index nor a ledger row names this identity — a deployment whose sessions all predate the
        booking journal, or have not sealed their first day yet, answers 200 with an empty list
        and says how many sessions it could not show)
    """
    rows = _ledger().read_rows(deployment_id=deployment_id)
    runs = [run for run in _runs() if run.parent_id == deployment_id]
    periods = compose_deployment_booking_periods(deployment_id, rows, runs)
    if periods is None:
        raise api_error(DEPLOYMENT_NOT_FOUND, deployment_id=deployment_id)
    return periods
