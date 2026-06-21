"""
Run-summary report IO (#390 prework).

Persist the run-wide KPI summary as JSON in the run directory (the API's source) and read it
back. JSON-only (the array + global counts are not a single flat CSV).
"""

from pathlib import Path

from python.framework.types.api.report_types import RunSummary

# Canonical artifact name inside a run directory
RUN_SUMMARY_ARTIFACT = 'run_summary.json'


def write_run_summary(report: RunSummary, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built run-summary report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / RUN_SUMMARY_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_run_summary(path: Path) -> RunSummary:
    """Read a persisted run-summary report artifact."""
    return RunSummary.model_validate_json(Path(path).read_text())
