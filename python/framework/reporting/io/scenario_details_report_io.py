"""
Scenario-details report IO (#391/#393).

Persist the scenario-details report as JSON in the run directory (the API's source) and read
it back. JSON-only (sim-only diagnostic section).
"""

from pathlib import Path

from python.framework.types.api.report_types import ScenarioDetailsReport

# Canonical artifact name inside a run directory
SCENARIO_DETAILS_ARTIFACT = 'scenario_details.json'


def write_scenario_details_report(report: ScenarioDetailsReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built scenario-details report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / SCENARIO_DETAILS_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_scenario_details_report(path: Path) -> ScenarioDetailsReport:
    """Read a persisted scenario-details report artifact."""
    return ScenarioDetailsReport.model_validate_json(Path(path).read_text())
