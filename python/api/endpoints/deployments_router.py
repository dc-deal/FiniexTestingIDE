"""
Deployments API router (#539) — read-only access to a live bot's life across its restarts.

A deployment is not a run, it is the sequence of them one bot produced: each restart writes its
own ledger row under its own run id, and #497 gave those rows a common identity so they can be
read back as one history. Until this router the only reader was the CLI, which prints — so the
viewer could show a backtest run and nothing at all about a thirty-day bot.

Served from the run-results ledger, and from nothing else. That is the rule the design follows
(operator, 2026-09-22): one route, one store. A deployment has no run directory, no header and
no artifacts of its own — asking the report store about one would be asking the wrong store.

What a deployment view deliberately does NOT have is a reconciliation line. Over many runs there
is no single run summary to sum against, so the second, independent derivation that makes a
check a check does not exist. It is stated rather than left out: a missing check read as a
passed one is the more expensive mistake.
"""

from pathlib import Path

from fastapi import APIRouter

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.api_errors import ApiException
from python.framework.reporting.builders.booking_periods_report_builder import (
    booking_periods_from_ledger_rows,
)
from python.framework.reporting.builders.deployment_history_builder import (
    build_deployment_histories,
    deployment_comparability_advisory,
    summarize_deployments,
)
from python.framework.reporting.store.run_completion_audit import unfinished_by_group
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import (
    DeploymentBookingPeriodsResponse,
    DeploymentDetailResponse,
    DeploymentListResponse,
    ParentKind,
)

router = APIRouter()


def _ledger() -> RunResultsLedger:
    """The run-results ledger at its configured location — a deployment's rows live here."""
    return RunResultsLedger(Path(AppConfigManager().get_run_ledger_path()))


@router.get('/deployments', response_model=DeploymentListResponse)
def list_deployments() -> DeploymentListResponse:
    """
    Every recorded deployment, newest first.

    Returns:
        The DeploymentListResponse — empty when no profile has declared a continuous
        deployment yet, which is a state and not a failure
    """
    rows = _ledger().read_rows()
    histories = build_deployment_histories(rows)
    advisories = {
        name: deployment_comparability_advisory(
            [r for r in rows if r.deployment_id == name])
        for name in histories
    }
    summaries = summarize_deployments(histories, advisories)
    return DeploymentListResponse(deployments=summaries, count=len(summaries))


@router.get('/deployments/{deployment_id}', response_model=DeploymentDetailResponse)
def get_deployment(deployment_id: str) -> DeploymentDetailResponse:
    """
    One deployment's sessions, oldest first — a life reads forwards.

    Args:
        deployment_id: The identity the sessions name

    Returns:
        The sessions, the comparability advisory when the configuration moved, and how many of
        this deployment's runs never reached their close (404 when no row names this identity)
    """
    # Filtered in the STORE, not after reading: `deployment_id` is the live counterpart of the
    # sweep filter, and its absence is why a live row was written and unreachable (§44).
    rows = _ledger().read_rows(deployment_id=deployment_id)
    if not rows:
        raise ApiException(
            status_code=404, error='deployment_not_found',
            detail=f"No deployment '{deployment_id}' in the run-results ledger")

    histories = build_deployment_histories(rows)
    sessions = histories.get(deployment_id, [])

    # The sessions come from the LEDGER, whose row is written last, so a run killed before its
    # close is absent from them by construction. The run index registered it at start, so the
    # count is recoverable — and a session count with no such note is a number the reader has
    # no reason to doubt. DEPLOYMENT explicitly: a sweep id has the same shape, so the kind is
    # what makes the filter exact (#386).
    file_logging = AppConfigManager().get_file_logging_config_object()
    runs = RunIndex(file_logging.run_index, file_logging.run_logs).list_runs()
    missing = unfinished_by_group(
        runs, rows, parent_id=deployment_id, parent_kind=ParentKind.DEPLOYMENT)

    return DeploymentDetailResponse(
        deployment_id=deployment_id,
        sessions=sessions,
        count=len(sessions),
        unfinished=sum(len(group) for group in missing.values()),
        advisory=deployment_comparability_advisory(rows),
    )


@router.get('/deployments/{deployment_id}/booking-periods',
            response_model=DeploymentBookingPeriodsResponse)
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
        The periods, oldest first within each currency (404 when no row names this identity —
        a deployment whose sessions all predate the booking journal answers 200 with an empty
        list and says how many sessions it could not show)
    """
    rows = _ledger().read_rows(deployment_id=deployment_id)
    if not rows:
        raise ApiException(
            status_code=404, error='deployment_not_found',
            detail=f"No deployment '{deployment_id}' in the run-results ledger")

    periods = booking_periods_from_ledger_rows(rows)
    # Counted over distinct RUNS, not rows: a run writes one row per period, so counting rows
    # would report a thirty-day session as thirty sessions. The second count is what keeps a
    # short list honest — a session whose row predates the booking journal books nothing.
    booked = {row.run_id for row in rows if row.segment_opened_at}
    return DeploymentBookingPeriodsResponse(
        deployment_id=deployment_id,
        periods=periods,
        count=len(periods),
        currencies=sorted({period.currency for period in periods}),
        sessions=len(booked),
        sessions_without_periods=len({row.run_id for row in rows} - booked),
    )
