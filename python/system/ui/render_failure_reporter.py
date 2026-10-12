"""
FiniexTestingIDE - Render Failure Reporter
Reports what a live screen could not draw — once per failure, never silently (#586).
"""

import sys
import traceback
from typing import Callable, Dict, Optional, Set, Tuple


class RenderFailureReporter:
    """
    Tells a live screen's failures to a log without burying it.

    A screen redraws several times a second, so a panel that fails, fails on every frame for
    as long as the run lasts. Each distinct failure — same place, same exception type, same
    message — is reported once, and each place at most `max_per_area` times; the last report
    says that further ones are not.

    Args:
        sink: Receives each report — a log's error channel; None reports nothing
        max_per_area: How many distinct failures of one place are reported
    """

    def __init__(self, sink: Optional[Callable[[str], None]], max_per_area: int = 3):
        self._sink = sink
        self._max_per_area = max_per_area
        self._reported: Set[Tuple[str, str, str]] = set()
        self._per_area: Dict[str, int] = {}

    def report(self, where: str, error: Exception, consequence: str) -> None:
        """
        Report one failure, unless it was reported before or its place has had its share.

        Args:
            where: What failed — a panel, a frame, a screen
            error: The failure
            consequence: What stands on screen instead, and what goes on regardless
        """
        if self._sink is None:
            return
        key = (where, type(error).__name__, str(error))
        reported = self._per_area.get(where, 0)
        if key in self._reported or reported >= self._max_per_area:
            return
        self._reported.add(key)
        self._per_area[where] = reported + 1
        last = (f' Further failures of {where} are not reported.'
                if reported + 1 == self._max_per_area else '')
        trace = ''.join(traceback.format_exception(error)).rstrip()
        try:
            self._sink(f'❌ [DISPLAY] {where} failed — {type(error).__name__}: {error}. '
                       f'{consequence}{last}\n{trace}')
        except Exception as report_error:
            # The log is the reporting path itself: stderr is what is left
            print(f'Warning: a display failure could not be reported: {report_error}',
                  file=sys.stderr)
