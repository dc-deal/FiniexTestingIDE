"""
Robustness report IO (#367).

Persist the multi-window + IS/OOS robustness report as JSON in the run directory (the API's
source) and read it back. JSON-only (the per-window rows + role aggregates are not a flat CSV).
"""
from pathlib import Path

from python.framework.types.api.report_types import RobustnessReport

# Canonical artifact name inside a run directory
ROBUSTNESS_ARTIFACT = 'robustness.json'


def write_robustness_report(report: RobustnessReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built robustness report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / ROBUSTNESS_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_robustness_report(path: Path) -> RobustnessReport:
    """Read a persisted robustness report artifact."""
    return RobustnessReport.model_validate_json(Path(path).read_text())
