"""
The divergence report's own record (#512): one directory per comparison under the run tree.

A comparison keeps what it ran and what it found: the scenario set it generated for each segment,
the run index it read a copy's sessions through, the report as JSON and as the text the console
printed. An A/A of two code stands keeps its sets and its report the same way. The backtests either
ran are runs of their own and are named in the report, never copied.
"""

from datetime import datetime, timezone
from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager
from python.framework.constants.divergence_aa_constants import AA_REPORT_FILE, AA_REPORT_TEXT_FILE
from python.framework.constants.divergence_report_constants import (
    DIVERGENCE_REPORT_FILE,
    DIVERGENCE_REPORT_TEXT_FILE,
    DIVERGENCE_SUBDIR,
)
from python.framework.types.api.divergence_report_types import AaReport, DivergenceReport
from python.framework.utils.atomic_file_utils import write_text_atomic
from python.framework.utils.file_utils import create_directory
from python.framework.utils.run_id_utils import mint_run_id


def divergence_root() -> Path:
    """
    Where the comparisons are kept — beside the simulation runs, under the same run tree.

    Returns:
        The store's root
    """
    run_logs = AppConfigManager().get_file_logging_config_object().run_logs
    return Path(run_logs.simulation).parent / DIVERGENCE_SUBDIR


def new_report_directory() -> Path:
    """
    A new comparison's directory, its name a freshly minted id.

    Returns:
        The created directory
    """
    root = divergence_root()
    report_dir = root / mint_run_id(datetime.now(timezone.utc), root)
    create_directory(report_dir, holds="a divergence comparison's record")
    return report_dir


def write_report(report: DivergenceReport, text: str, report_dir: Path) -> None:
    """
    Keep a comparison's report beside what it ran.

    Args:
        report: The report
        text: The report as the console printed it
        report_dir: The comparison's directory
    """
    write_text_atomic(report_dir / DIVERGENCE_REPORT_FILE, report.model_dump_json(indent=2))
    write_text_atomic(report_dir / DIVERGENCE_REPORT_TEXT_FILE, text)


def write_aa_report(report: AaReport, text: str, report_dir: Path) -> None:
    """
    Keep an A/A's report beside the scenario sets it ran.

    Args:
        report: The A/A
        text: The report as the console printed it
        report_dir: The A/A's directory
    """
    write_text_atomic(report_dir / AA_REPORT_FILE, report.model_dump_json(indent=2))
    write_text_atomic(report_dir / AA_REPORT_TEXT_FILE, text)
