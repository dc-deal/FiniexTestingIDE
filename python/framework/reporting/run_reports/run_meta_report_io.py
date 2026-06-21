"""
Run-meta report IO.

Persist the run-level execution meta (timing split + scenario identity) as JSON in the run
directory (the API's source) and read it back. JSON-only.
"""

from pathlib import Path

from python.framework.types.api.report_types import RunMetaReport

# Canonical artifact name inside a run directory
RUN_META_ARTIFACT = 'run_meta.json'


def write_run_meta_report(report: RunMetaReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built run-meta report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / RUN_META_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_run_meta_report(path: Path) -> RunMetaReport:
    """Read a persisted run-meta report artifact."""
    return RunMetaReport.model_validate_json(Path(path).read_text())
