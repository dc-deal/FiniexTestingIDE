"""
Execution-stats report IO (#391).

Persist the execution-stats report as JSON (the API's source) plus a flat CSV of the
per-unit rows, and read the JSON back. The totals stay a JSON section; the CSV is the
per-unit table (same rule as the trade/order CSV).
"""

import csv
from pathlib import Path

from python.framework.types.api.report_types import ExecutionStatsReport, ExecutionStatsRow

# Canonical artifact names inside a run directory
EXECUTION_STATS_ARTIFACT = 'execution_stats.json'
EXECUTION_STATS_CSV = 'execution_stats.csv'


def write_execution_stats_report(report: ExecutionStatsReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built execution-stats report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / EXECUTION_STATS_ARTIFACT
    path.write_text(report.model_dump_json(indent=2), encoding='utf-8')
    return path


def read_execution_stats_report(path: Path) -> ExecutionStatsReport:
    """Read a persisted execution-stats report artifact."""
    return ExecutionStatsReport.model_validate_json(Path(path).read_bytes())


def write_execution_stats_csv(report: ExecutionStatsReport, run_dir: Path) -> Path:
    """
    Persist the per-unit rows as a CSV table (same columns as the JSON unit rows).

    Args:
        report: The built execution-stats report
        run_dir: The run's directory

    Returns:
        Path of the written CSV
    """
    path = Path(run_dir) / EXECUTION_STATS_CSV
    # `run_id` leads every row: a CSV is the format that gets exported and merged, and
    # a row without its run is a row nobody can trace back (#475).
    columns = ['run_id'] + list(ExecutionStatsRow.model_fields)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in report.units:
            writer.writerow({'run_id': report.run_id, **row.model_dump()})
    return path
