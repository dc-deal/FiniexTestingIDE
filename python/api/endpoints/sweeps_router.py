"""
Sweeps API router — read-only access to recorded parameter sweeps.

A sweep is not a run, it is a family of them, and the two want different views: the run index
(`/reports/runs`) lists standalone runs, this router lists sweeps and ranks their combinations.
Served from the run-results ledger (#390), which is where a sweep's identity and KPIs live —
the logs tree only knows directory names.

Each combination row carries its `run_id`, so a consumer drills from a sweep straight into the
existing report routes without this router duplicating any of them.
"""

from pathlib import Path

from fastapi import APIRouter

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.api_errors import ApiException
from python.framework.optimization.optimization_analysis import rank, summarize_sweeps
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import SweepDetailResponse, SweepListResponse

router = APIRouter()


def _ledger() -> RunResultsLedger:
    """The run-results ledger at its configured location — a sweep's identity lives here."""
    return RunResultsLedger(Path(AppConfigManager().get_run_ledger_path()))


@router.get('/sweeps', response_model=SweepListResponse)
def list_sweeps() -> SweepListResponse:
    """
    Every recorded parameter sweep, newest first.

    Returns:
        The SweepListResponse (empty list when no sweep has been recorded yet)
    """
    # summarize_sweeps orders chronologically (sweep_id is timestamp-based); newest first is
    # what a picker wants, and it matches the run index's ordering.
    sweeps = list(reversed(summarize_sweeps(_ledger().read_rows())))
    return SweepListResponse(sweeps=sweeps, count=len(sweeps))


@router.get('/sweeps/{sweep_id}', response_model=SweepDetailResponse)
def get_sweep(sweep_id: str) -> SweepDetailResponse:
    """
    One sweep's combinations, ranked by the objective the sweep declared.

    Args:
        sweep_id: The sweep's id

    Returns:
        The ranked combinations, best first
    """
    rows = _ledger().read_rows(sweep_id=sweep_id)
    if not rows:
        raise ApiException(
            status_code=404, error='sweep_not_found',
            detail=f"No sweep '{sweep_id}' in the run-results ledger")

    # The spec's own objective and direction were recorded with the runs — ranking by anything
    # else here would answer a question the sweep did not ask.
    head = rows[0]
    objective = head.sweep_objective or 'expectancy'
    maximize = head.sweep_maximize if head.sweep_maximize is not None else True
    combinations = rank(rows, objective, maximize=maximize)
    return SweepDetailResponse(
        sweep_id=sweep_id, objective=objective, maximize=maximize,
        combinations=combinations, count=len(combinations))
