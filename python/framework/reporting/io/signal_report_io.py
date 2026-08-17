"""
Signal report IO (#433).

Persist the signal-configuration report as JSON in the run directory (the API's source)
and read it back. JSON-only — the section is a source-keyed snapshot plus nested usage
rows, which does not flatten into one CSV table.
"""

from pathlib import Path

from python.framework.types.api.report_types import SignalReport

# Canonical artifact name inside a run directory
SIGNAL_ARTIFACT = 'signal.json'


def write_signal_report(report: SignalReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built signal report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / SIGNAL_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_signal_report(path: Path) -> SignalReport:
    """Read a persisted signal report artifact."""
    return SignalReport.model_validate_json(Path(path).read_text())
