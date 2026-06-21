"""
Pending-orders report IO (#391).

Persist the pending-orders report as JSON in the run directory (the API's source) and read
it back. JSON-only — the per-unit rows nest active-order lists (same rule as the portfolio
report), so there is no flat CSV.
"""

from pathlib import Path

from python.framework.types.api.report_types import PendingOrdersReport

# Canonical artifact name inside a run directory
PENDING_ORDERS_ARTIFACT = 'pending_orders.json'


def write_pending_orders_report(report: PendingOrdersReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built pending-orders report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / PENDING_ORDERS_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_pending_orders_report(path: Path) -> PendingOrdersReport:
    """Read a persisted pending-orders report artifact."""
    return PendingOrdersReport.model_validate_json(Path(path).read_text())
