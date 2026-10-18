"""
Session Log Rotator — one session log file per trading day (#476).

The first pass names the file for the trading day the canonical clock stands in; a pass on a new
trading day opens the next file and says so in the old one. The day comes from the session's own
trading-day function, never from a tick stamp, and a session without a run directory writes no
file at all.
"""

from datetime import date
from typing import List, Optional

from python.framework.autotrader.session_log_rotator import SessionLogRotator


class _SessionLogger:
    """The two things the rotator does to the session logger: swap its file, write a line."""

    def __init__(self):
        self.files: List[str] = []
        self.lines: List[str] = []

    def swap_file_logger(self, file_logger) -> None:
        self.files.append(file_logger.log_file_path.name)

    def info(self, message: str) -> None:
        self.lines.append(message)

    def warning(self, message: str) -> None:
        self.lines.append(message)


class _TradingDay:
    """The session's trading-day function, set by the test."""

    def __init__(self, day: Optional[date]):
        self.day = day

    def __call__(self) -> Optional[date]:
        return self.day


def test_two_passes_across_a_boundary_open_two_files(tmp_path):
    logger, trading_day = _SessionLogger(), _TradingDay(date(2026, 10, 13))
    rotator = SessionLogRotator(tmp_path, logger, trading_day)

    rotator.check_daily_rotation()
    rotator.check_daily_rotation()
    assert logger.files == ['autotrader_session_20261013.log'], 'one file per day, not per pass'

    trading_day.day = date(2026, 10, 14)
    rotator.check_daily_rotation()
    assert logger.files == ['autotrader_session_20261013.log', 'autotrader_session_20261014.log']
    assert any('20261013 → 20261014' in line for line in logger.lines)
    assert (tmp_path / 'session_logs').is_dir()


def test_nothing_rotates_before_the_clock_is_set(tmp_path):
    logger = _SessionLogger()
    SessionLogRotator(tmp_path, logger, _TradingDay(None)).check_daily_rotation()
    assert logger.files == []


def test_a_session_without_a_run_directory_writes_no_file():
    logger = _SessionLogger()
    SessionLogRotator(None, logger, _TradingDay(date(2026, 10, 13))).check_daily_rotation()
    assert logger.files == []
