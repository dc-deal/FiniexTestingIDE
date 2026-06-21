"""
Warnings & errors report IO (#391/#395).

Persist the warnings/errors report as JSON in the run directory (the API's source) and read it
back. JSON-only (advisory section — no CSV surface).
"""

from pathlib import Path

from python.framework.types.api.report_types import WarningsErrorsReport

# Canonical artifact name inside a run directory
WARNINGS_ERRORS_ARTIFACT = 'warnings_errors.json'


def write_warnings_errors_report(report: WarningsErrorsReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built warnings/errors report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / WARNINGS_ERRORS_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_warnings_errors_report(path: Path) -> WarningsErrorsReport:
    """Read a persisted warnings/errors report artifact."""
    return WarningsErrorsReport.model_validate_json(Path(path).read_text())
