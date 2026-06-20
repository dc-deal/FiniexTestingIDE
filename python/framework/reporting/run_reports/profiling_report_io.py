"""
Profiling report IO (#399).

JSON-only — the nested operation / inter-tick / clipping rows do not flatten into a single CSV
row (same rule as the portfolio + worker-decision models). Write the artifact in the run
directory; read it back (the API path). Sim-only — written by the batch coordinator.
"""

from pathlib import Path

from python.framework.types.api.report_types import ProfilingReport

# Canonical artifact name inside a run directory
PROFILING_ARTIFACT = 'profiling.json'


def write_profiling_report(report: ProfilingReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built profiling report
        run_dir: The run's directory (sim scenario-set run dir)

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / PROFILING_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_profiling_report(path: Path) -> ProfilingReport:
    """Read a persisted profiling report artifact."""
    return ProfilingReport.model_validate_json(Path(path).read_text())
