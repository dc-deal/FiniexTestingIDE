"""
What a production of a catalog entry is checked against (#576).

Read through the readers the API serves from, so a property holds for what a consumer is SERVED:
a run's sections from the report store the report routes read, its latest state frames composed by
the function the check-in route answers with, a unit's recorded streams composed by the function
the timeline route answers with, a deployment and a sweep from the very route functions that answer
for them. Called as functions, never over HTTP, which would need a token the
producing machine does not hold for itself.
"""

from datetime import datetime, timezone
from typing import Dict, Iterable, Optional

from pydantic import BaseModel

from python.api.endpoints.deployments_router import (
    get_deployment,
    get_deployment_booking_periods,
    list_deployments,
)
from python.api.endpoints.sweeps_router import get_sweep
from python.api.served_recorded_streams import describe_unit_recorded_streams
from python.api.served_state_frames import describe_latest_state_frames
from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.api_errors import ApiException
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.artifact_specs import (
    PORTFOLIO_ARTIFACT,
    RUN_SUMMARY_ARTIFACT,
    WARNINGS_ERRORS_ARTIFACT,
)
from python.framework.reporting.store.report_store import ReportStore
from python.framework.types.api.recorded_stream_types import UnitRecordedStreams
from python.framework.types.api.report_types import RunInfo, WarningsErrorsReport
from python.framework.types.api.state_frame_types import LatestStateFramesResponse
from python.framework.types.config_types.state_frames_api_config_types import StateFramesApiConfig
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
    thresholds = AppConfigManager().get_state_frames_api_config()
    served_at = datetime.now(timezone.utc)
    for run in evidence.runs:
        run_id = run.run_id
        _put(evidence.order_history, run_id, store.get_order_history(run_id))
        _put(evidence.trade_history, run_id, store.get_trade_history(run_id))
        _put(evidence.run_summary, run_id, store.get(run_id, RUN_SUMMARY_ARTIFACT))
        _put(evidence.warnings_errors, run_id, store.get(run_id, WARNINGS_ERRORS_ARTIFACT))
        _put(evidence.portfolio, run_id, store.get(run_id, PORTFOLIO_ARTIFACT))
        _put(evidence.latest_state_frames, run_id, _latest_state_frames(
            store, run, evidence.warnings_errors.get(run_id), thresholds, served_at))
        _put(evidence.recorded_streams, run_id, _recorded_streams(
            store, run, thresholds, served_at))

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


def _latest_state_frames(
    store: ReportStore,
    run: RunInfo,
    warnings_errors: Optional[WarningsErrorsReport],
    thresholds: StateFramesApiConfig,
    served_at: datetime,
) -> Optional[LatestStateFramesResponse]:
    """
    A run's latest state frames, composed the way the check-in route answers for them.

    Args:
        store: The report store
        run: The run's row in the run list
        warnings_errors: Its warnings and errors, for how it ended; None when it has none
        thresholds: How many cadences a frame may age
        served_at: The moment the evidence is read, on the wall clock

    Returns:
        The answer, or None when the run has no frame or its stream cannot be read
    """
    try:
        ends = store.get_state_frame_ends(run.run_id)
    except ReportArtifactUnreadableError:
        return None
    if ends is None or ends.last is None:
        return None
    outcome = warnings_errors.outcome if warnings_errors is not None else None
    return describe_latest_state_frames(ends, run, outcome, thresholds, served_at)


def _recorded_streams(
    store: ReportStore,
    run: RunInfo,
    thresholds: StateFramesApiConfig,
    served_at: datetime,
) -> Optional[UnitRecordedStreams]:
    """
    A run's unit timeline, composed the way the timeline route answers for it.

    A session records one unit, so its timeline is the run's; its name comes from the stream
    headers, as the route resolves the unit it is asked for.

    Args:
        store: The report store
        run: The run's row in the run list
        thresholds: How many cadences a frame may age
        served_at: The moment the evidence is read, on the wall clock

    Returns:
        The timeline, or None when the run records no stream or one cannot be read
    """
    try:
        frames = store.get_state_frame_overview(run.run_id)
        series = [(worker, store.get_indicator_series_overview(run.run_id, worker))
                  for worker in store.get_indicator_series_workers(run.run_id)]
    except ReportArtifactUnreadableError:
        return None
    series = [(worker, overview) for worker, overview in series if overview is not None]
    if frames is None and not series:
        return None
    unit_name = (frames.ends.unit_name if frames is not None
                 else next((overview.header.unit_name for _, overview in series
                            if overview.header is not None), None))
    if unit_name is None:
        return None
    return describe_unit_recorded_streams(frames, series, unit_name, run, thresholds, served_at)


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
