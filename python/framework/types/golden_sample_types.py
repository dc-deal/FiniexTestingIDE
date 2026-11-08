"""
Golden sample types (#557) — what a frozen sample holds, and how one of its aggregates is checked.

A golden sample freezes a verified production of a fixture catalog entry: the report sections and
ledger rows its aggregates are computed from, the read-time views composed from those rows, and
where all of it came from — which runs, on which commit, when. A block is one aggregate the sample
checks: rebuilt from the frozen inputs through today's code and compared field by field with what
the run itself recorded.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

from pydantic import BaseModel

from python.framework.types.api.report_types import (
    AggregatedPortfolioReport,
    BookingPeriodsReport,
    DeploymentBookingPeriodsResponse,
    DeploymentDetailResponse,
    DeploymentSummary,
    ExecutionStatsReport,
    FeedStabilityReport,
    OrderHistoryReport,
    PendingOrdersReport,
    PortfolioReport,
    ProfilingReport,
    RunInfo,
    RunListFigures,
    RunResultRow,
    RunSummary,
    ScenarioDetailsReport,
    SignalReport,
    SweepDetailResponse,
    SweepSummary,
    TradeHistoryReport,
    WorkerDecisionReport,
)


class GoldenSampleRun(BaseModel):
    """
    One run of the frozen production, and the code it ran on — read from its own header.

    Args:
        run_id: The run
        run_type: Which pipeline produced it; None when the run holds no header
        started_at: When it started; None when the run holds no header
        commit: The commit of this repository it ran from; None when the header does not say
        branch: The branch checked out when it started; None when the header does not say
        dirty: Whether the working tree differed from that commit; None when unknown
        diff_hash: The header's hash over the content of every uncommitted change — with the
            commit it names the code of a run made on a dirty tree; None on a clean one
        sections: The report section files frozen for it — empty where none are frozen
    """
    run_id: str
    run_type: Optional[str] = None
    started_at: Optional[datetime] = None
    commit: Optional[str] = None
    branch: Optional[str] = None
    dirty: Optional[bool] = None
    diff_hash: Optional[str] = None
    sections: List[str] = []


class GoldenSampleHeader(BaseModel):
    """
    Where a golden sample comes from: which production of which entry, which code, and when.

    A sample is a snapshot of numbers that today's code must still produce; what makes it worth
    keeping is knowing what it is a snapshot OF. A sample nobody can date is one nobody dares to
    replace or to delete.

    Args:
        entry_id: The fixture catalog entry
        produced_at: When the production ran, as the production record has it
        report_contract: The API contract the production's reports were written under
        frozen_at: When the sample was frozen — a provenance stamp off the wall clock
        composed_at: The instant the read-time views were composed at; the deployment code
            summaries measure against it, so the rebuild uses it too
        runs: The production's runs, with their code
        deployment_ids: The deployments its sessions belong to
        sweep_ids: The sweeps it ran
    """
    entry_id: str
    produced_at: str
    report_contract: int
    frozen_at: datetime
    composed_at: datetime
    runs: List[GoldenSampleRun]
    deployment_ids: List[str] = []
    sweep_ids: List[str] = []


class GoldenSampleInputs(BaseModel):
    """
    What the read-time views are composed from.

    Args:
        ledger_rows: The production's ledger rows, as the ledger reads them back
        run_index: The production's rows of the run index — a session killed before its first
            seal has no ledger row and is known only there
    """
    ledger_rows: List[RunResultRow] = []
    run_index: List[RunInfo] = []


class GoldenSampleViews(BaseModel):
    """
    The read-time views of the production, as the API composed them from the real stores.

    Args:
        run_list_figures: The run list's figures, per run
        deployments: The deployment list's rows of the production's deployments
        deployment_details: Each deployment's detail
        deployment_booking_periods: Each deployment's booking periods
        sweeps: The sweep list's rows of the production's sweeps
        sweep_details: Each sweep's ranked combinations
    """
    run_list_figures: Dict[str, RunListFigures] = {}
    deployments: List[DeploymentSummary] = []
    deployment_details: Dict[str, DeploymentDetailResponse] = {}
    deployment_booking_periods: Dict[str, DeploymentBookingPeriodsResponse] = {}
    sweeps: List[SweepSummary] = []
    sweep_details: Dict[str, SweepDetailResponse] = {}


@dataclass
class FrozenRun:
    """
    One run's frozen report sections and ledger rows — a section the run did not write is None.

    Args:
        run_id: The run
        ledger_rows: Its rows in the ledger
    """
    run_id: str
    portfolio: Optional[PortfolioReport] = None
    trade_history: Optional[TradeHistoryReport] = None
    order_history: Optional[OrderHistoryReport] = None
    execution_stats: Optional[ExecutionStatsReport] = None
    pending_orders: Optional[PendingOrdersReport] = None
    worker_decision: Optional[WorkerDecisionReport] = None
    profiling: Optional[ProfilingReport] = None
    signal: Optional[SignalReport] = None
    feed_stability: Optional[FeedStabilityReport] = None
    booking_periods: Optional[BookingPeriodsReport] = None
    aggregated_portfolio: Optional[AggregatedPortfolioReport] = None
    run_summary: Optional[RunSummary] = None
    scenario_details: Optional[ScenarioDetailsReport] = None
    ledger_rows: List[RunResultRow] = field(default_factory=list)


@dataclass
class GoldenSample:
    """
    A whole golden sample, read back from its entry folder.

    Args:
        header: Where it comes from
        runs: The production's runs with their frozen sections, in the header's order
        inputs: What the read-time views are composed from
        views: The read-time views as the API composed them
    """
    header: GoldenSampleHeader
    runs: List[FrozenRun]
    inputs: GoldenSampleInputs
    views: GoldenSampleViews


def _nothing_left_out(run: FrozenRun) -> FrozenSet[str]:
    """
    The default for a block whose rebuild recomputes every field.

    Args:
        run: The run

    Returns:
        No field
    """
    return frozenset()


@dataclass(frozen=True)
class GoldenSampleBlock:
    """
    One aggregate a golden sample checks, run by run.

    Args:
        block_id: Its identity, as the reports name the aggregate
        title: One line on what it is
        needs: The FrozenRun fields that must be present for the block to apply to a run
        rebuild: The aggregate rebuilt from the run's frozen inputs, through today's code
        recorded: The aggregate as the run itself recorded it
        left_out: The top-level fields the rebuild cannot recompute for this run — named, never
            silently skipped; each block that leaves one out says why
    """
    block_id: str
    title: str
    needs: Tuple[str, ...]
    rebuild: Callable[[FrozenRun], Any]
    recorded: Callable[[FrozenRun], Any]
    left_out: Callable[[FrozenRun], FrozenSet[str]] = _nothing_left_out


@dataclass(frozen=True)
class GoldenSampleDifference:
    """
    One field where a rebuild does not reproduce what was recorded.

    Args:
        entry_id: The catalog entry
        check: The block or view that was checked
        subject: The run, deployment or sweep it was checked for
        path: Where in the aggregate the two part, as `field[index].field`
        recorded: The recorded value
        rebuilt: The rebuilt value
    """
    entry_id: str
    check: str
    subject: str
    path: str
    recorded: Any
    rebuilt: Any


@dataclass(frozen=True)
class GoldenSampleFreeze:
    """
    What one freeze wrote, for the console.

    Args:
        folder: The entry folder written
        header: The sample's header
        block_checks: How many block-and-run pairs were rebuilt and compared before writing
        view_subjects: How many runs, deployments and sweeps the read-time views were checked for
    """
    folder: Path
    header: GoldenSampleHeader
    block_checks: int
    view_subjects: int
