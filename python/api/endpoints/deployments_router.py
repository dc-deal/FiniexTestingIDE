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
from python.framework.reporting.builders.booking_periods_report_builder import (
    booking_periods_from_ledger_rows,
)
from python.framework.reporting.builders.deployment_history_builder import (
    add_unbooked_sessions,
    build_deployment_histories,
    deployment_code_summary,
    deployment_comparability_advisory,
    summarize_deployments,
    unbooked_runs,
)
from python.framework.reporting.store.run_completion_audit import unfinished_by_group
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import (
    DeploymentBookingPeriodsResponse,
    DeploymentDetailResponse,
    DeploymentListResponse,
    ParentKind,
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
    rows = _ledger().read_rows()
    histories = add_unbooked_sessions(build_deployment_histories(rows), rows, _runs())
    advisories = {
        name: deployment_comparability_advisory(
            [r for r in rows if r.deployment_id == name])
        for name in histories
    }
    now = datetime.now(timezone.utc)
    code_summaries = {
        name: deployment_code_summary([r for r in rows if r.deployment_id == name], now)
        for name in histories
    }
    summaries = summarize_deployments(histories, advisories, code_summaries)
    return DeploymentListResponse(deployments=summaries, count=len(summaries))


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
    sessions = add_unbooked_sessions(
        build_deployment_histories(rows), rows, runs).get(deployment_id, [])
    if not sessions:
        raise api_error(DEPLOYMENT_NOT_FOUND, deployment_id=deployment_id)

    # The ledger holds the row of a session's close only at the end: a run killed before it is
    # in the list without an end when it sealed a day, and with no figures when it sealed none.
    # The count says how many such runs there are — without it a session that died reads like
    # one still running. DEPLOYMENT explicitly: a sweep id has the same shape, so the kind is
    # what makes the filter exact (#386).
    missing = unfinished_by_group(
        runs, rows, parent_id=deployment_id, parent_kind=ParentKind.DEPLOYMENT)

    return DeploymentDetailResponse(
        deployment_id=deployment_id,
        sessions=sessions,
        count=len(sessions),
        unfinished=sum(len(group) for group in missing.values()),
        advisory=deployment_comparability_advisory(rows),
        code_summary=deployment_code_summary(rows, datetime.now(timezone.utc)),
    )


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
    not_booked = unbooked_runs(
        rows, [run for run in _runs() if run.parent_id == deployment_id]).get(deployment_id, [])
    if not rows and not not_booked:
        raise api_error(DEPLOYMENT_NOT_FOUND, deployment_id=deployment_id)

    periods = booking_periods_from_ledger_rows(rows)
    # Counted over distinct RUNS, not rows: a run writes one row per period, so counting rows
    # would report a thirty-day session as thirty sessions. The second count is what keeps a
    # short list honest — a session whose row predates the booking journal books nothing, and
    # neither has one that has not sealed its first day yet.
    booked = {row.run_id for row in rows if row.period_opened_at}
    return DeploymentBookingPeriodsResponse(
        deployment_id=deployment_id,
        periods=periods,
        count=len(periods),
        currencies=sorted({period.currency for period in periods}),
        sessions=len(booked),
        sessions_without_periods=len(
            ({row.run_id for row in rows} - booked) | {run.run_id for run in not_booked}),
    )
