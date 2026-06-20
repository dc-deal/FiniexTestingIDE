"""
Aggregated-portfolio report IO (#397).

Persist the aggregated per-currency portfolio report as JSON in the run directory (the API's
source) and read it back. JSON-only (the per-currency detail view — no flat CSV surface).
"""

from pathlib import Path

from python.framework.types.api.report_types import AggregatedPortfolioReport

# Canonical artifact name inside a run directory
AGGREGATED_PORTFOLIO_ARTIFACT = 'aggregated_portfolio.json'


def write_aggregated_portfolio_report(report: AggregatedPortfolioReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built aggregated-portfolio report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / AGGREGATED_PORTFOLIO_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_aggregated_portfolio_report(path: Path) -> AggregatedPortfolioReport:
    """Read a persisted aggregated-portfolio report artifact."""
    return AggregatedPortfolioReport.model_validate_json(Path(path).read_text())
