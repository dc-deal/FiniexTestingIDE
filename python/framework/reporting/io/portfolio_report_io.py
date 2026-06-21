"""
Portfolio report IO (#391).

Persist the built portfolio report as JSON in the run directory (the API's source)
and read it back. The portfolio report is a two-section model (per-unit rows +
per-currency aggregates), so it is JSON-only — unlike the flat trade/order tables it
is not a single CSV table.
"""

from pathlib import Path

from python.framework.types.api.report_types import PortfolioReport

# Canonical artifact name inside a run directory
PORTFOLIO_ARTIFACT = 'portfolio.json'


def write_portfolio_report(report: PortfolioReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built portfolio report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / PORTFOLIO_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_portfolio_report(path: Path) -> PortfolioReport:
    """Read a persisted portfolio report artifact."""
    return PortfolioReport.model_validate_json(Path(path).read_text())
