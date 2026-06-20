"""
Broker report IO (#391).

Persist the broker-configuration report as JSON in the run directory (the API's source)
and read it back. JSON-only (a static config snapshot — no CSV surface).
"""

from pathlib import Path

from python.framework.types.api.report_types import BrokerReport

# Canonical artifact name inside a run directory
BROKER_ARTIFACT = 'broker.json'


def write_broker_report(report: BrokerReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built broker report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / BROKER_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_broker_report(path: Path) -> BrokerReport:
    """Read a persisted broker report artifact."""
    return BrokerReport.model_validate_json(Path(path).read_text())
