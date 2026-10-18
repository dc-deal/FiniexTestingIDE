"""
A coverage report stand-in for tests that drive the scenario validator.

The validator asks a tick coverage report by time window (`gap_containing`, `gaps_in_window`),
so a bare MagicMock would answer every question with another mock — which the validator reads
as a gap. The stub answers from the gaps it is given, through the same timeline the real report
uses.
"""

from typing import List, Optional
from unittest.mock import MagicMock

from python.framework.discoveries.data_coverage.gap_timeline import GapTimeline
from python.framework.types.coverage_report_types import Gap


def coverage_report_stub(gaps: Optional[List[Gap]] = None) -> MagicMock:
    """
    Build a coverage report stub whose gap list and window queries agree.

    Args:
        gaps: The report's gaps — none by default, a clean report

    Returns:
        The stub; set start_time, end_time or anything else the test reads on it
    """
    report = MagicMock()
    report.gaps = list(gaps or [])
    timeline = GapTimeline(report.gaps)
    report.gap_containing.side_effect = timeline.gap_containing
    report.gaps_in_window.side_effect = timeline.gaps_in_window
    return report
