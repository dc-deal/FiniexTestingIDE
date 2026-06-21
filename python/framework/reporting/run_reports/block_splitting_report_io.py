"""
Block-splitting report IO.

Persist the per-symbol block-splitting disposition (Profile Runs) as JSON in the run directory
(the API's source) and read it back. JSON-only.
"""

from pathlib import Path

from python.framework.types.api.report_types import BlockSplittingReport

# Canonical artifact name inside a run directory
BLOCK_SPLITTING_ARTIFACT = 'block_splitting.json'


def write_block_splitting_report(report: BlockSplittingReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built block-splitting report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / BLOCK_SPLITTING_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_block_splitting_report(path: Path) -> BlockSplittingReport:
    """Read a persisted block-splitting report artifact."""
    return BlockSplittingReport.model_validate_json(Path(path).read_text())
