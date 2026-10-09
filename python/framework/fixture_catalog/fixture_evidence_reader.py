"""
What a production of a catalog entry is checked against (#576).

Read through the readers the API serves from, so a property holds for what a consumer is SERVED:
a run's sections from the report store the report routes read, a deployment and a sweep from the
very route functions that answer for them. Called as functions, never over HTTP, which would need a
token the producing machine does not hold for itself.
"""

from typing import Dict, Iterable, Optional

from pydantic import BaseModel

from python.api.endpoints.deployments_router import (
    get_deployment,
    get_deployment_booking_periods,
    list_deployments,
)
from python.api.endpoints.sweeps_router import get_sweep
from python.framework.exceptions.api_errors import ApiException
from python.framework.reporting.io.artifact_specs import (
    PORTFOLIO_ARTIFACT,
    RUN_SUMMARY_ARTIFACT,
    WARNINGS_ERRORS_ARTIFACT,
)
from python.framework.reporting.store.report_store import ReportStore
from python.framework.types.fixture_catalog_types import FixtureEvidence


def read_fixture_evidence(run_ids: Iterable[str], deployment_ids: Iterable[str],
                          sweep_ids: Iterable[str]) -> FixtureEvidence:
    """
    Read everything a production's properties are checked against.

    A section a run did not produce is simply absent from its map — a property that needs it
    then does not hold, which is the answer the check exists to give.

    Args:
        run_ids: The runs the production made
        deployment_ids: The deployments its sessions belong to
        sweep_ids: The sweeps it ran

    Returns:
        The evidence
    """
    store = ReportStore()
    wanted = set(run_ids)
    evidence = FixtureEvidence(
        runs=[run for run in store.list_runs_with_results() if run.run_id in wanted])
    for run in evidence.runs:
        run_id = run.run_id
        _put(evidence.order_history, run_id, store.get_order_history(run_id))
        _put(evidence.trade_history, run_id, store.get_trade_history(run_id))
        _put(evidence.run_summary, run_id, store.get(run_id, RUN_SUMMARY_ARTIFACT))
        _put(evidence.warnings_errors, run_id, store.get(run_id, WARNINGS_ERRORS_ARTIFACT))
        _put(evidence.portfolio, run_id, store.get(run_id, PORTFOLIO_ARTIFACT))

    deployments = set(deployment_ids)
    if deployments:
        evidence.deployment_rows = [row for row in list_deployments().deployments
                                    if row.deployment_id in deployments]
    for deployment_id in deployments:
        try:
            evidence.deployments[deployment_id] = get_deployment(deployment_id)
            evidence.deployment_periods[deployment_id] = get_deployment_booking_periods(
                deployment_id)
        except ApiException:
            # A deployment whose every session died before its close has no ledger row and
            # answers 404 — the properties that need it then fail, by name.
            continue
    for sweep_id in sweep_ids:
        try:
            evidence.sweeps[sweep_id] = get_sweep(sweep_id)
        except ApiException:
            continue
    return evidence


def _put(target: Dict[str, BaseModel], run_id: str, report: Optional[BaseModel]) -> None:
    """
    Keep a section when the run produced it.

    Args:
        target: The evidence map for that section
        run_id: The run
        report: The section, or None when the run has none
    """
    if report is not None:
        target[run_id] = report
