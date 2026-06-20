"""
Worker/decision report IO (#398).

JSON-only — the nested per-worker rows do not flatten into a single CSV row (same rule as the
portfolio model). Write the artifact in the run directory; read it back (the API path).
"""

from pathlib import Path

from python.framework.types.api.report_types import WorkerDecisionReport

# Canonical artifact name inside a run directory
WORKER_DECISION_ARTIFACT = 'worker_decision.json'


def write_worker_decision_report(report: WorkerDecisionReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built worker/decision report
        run_dir: The run's directory (sim scenario-set run dir / autotrader run dir)

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / WORKER_DECISION_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_worker_decision_report(path: Path) -> WorkerDecisionReport:
    """Read a persisted worker/decision report artifact."""
    return WorkerDecisionReport.model_validate_json(Path(path).read_text())
