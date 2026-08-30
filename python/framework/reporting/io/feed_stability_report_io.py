"""
Feed stability report IO (#451).

Persist the feed-stability report as JSON in the run directory (the API's source) and
read it back. JSON-only — the section nests episode lists under each source, which does
not flatten into one CSV table.
"""

from pathlib import Path

from python.framework.types.api.report_types import FeedStabilityReport

# Canonical artifact name inside a run directory
FEED_STABILITY_ARTIFACT = 'feed_stability.json'


def write_feed_stability_report(report: FeedStabilityReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built feed-stability report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / FEED_STABILITY_ARTIFACT
    path.write_text(report.model_dump_json(indent=2), encoding='utf-8')
    return path


def read_feed_stability_report(path: Path) -> FeedStabilityReport:
    """Read a persisted feed-stability report artifact."""
    return FeedStabilityReport.model_validate_json(Path(path).read_bytes())
