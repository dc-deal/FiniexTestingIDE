"""
FiniexTestingIDE - Session Log Rotator (#476)

An AutoTrader session writes one log file per trading day, so a thirty-day session leaves thirty
files rather than one that grows for a month. This rotates the file when the market crosses into
a new trading day, and hands the rotated files to the retention rule beside it
(`session_log_retention.py`) — the two answer one concern, the session's own log files.
"""

from datetime import date
from pathlib import Path
from typing import Callable, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.autotrader.session_log_retention import prune_rotated_session_logs
from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.logging.file_logger import FileLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.file_utils import create_directory


class SessionLogRotator:
    """
    Rotates a session's log file at the market's trading-day boundary.

    The day comes from the session's own trading-day function — the canonical clock and this
    market's anchor — never from the tick stamp, so the log rotates on the same boundary the
    booking period and the daily loss limit use.

    Args:
        run_dir: The session run directory; None in a test that keeps no files
        logger: The session logger whose file is swapped
        trading_day: The trading day the canonical clock stands in, None before it is first set
    """

    def __init__(
        self,
        run_dir: Optional[Path],
        logger: ScenarioLogger,
        trading_day: Callable[[], Optional[date]],
    ):
        self._run_dir = run_dir
        self._logger = logger
        self._trading_day = trading_day
        # The run directory exists whether or not log files are written; the switch alone
        # decides whether a daily log is opened (#621). Read once, at the session's start.
        self._writes_logs = AppConfigManager().get_file_logging_config_object().scenario_enabled
        # Daily rotation: date initialized from the first pass (not wall clock). This prevents
        # spurious rotation in mock/replay mode where tick timestamps differ from current date.
        self._current_log_date: Optional[str] = None

    def has_opened_day_log(self) -> bool:
        """
        Whether a pass has opened a trading day's log; the session's close judges the day log by it.

        Returns:
            True once the first day's log is open
        """
        return self._current_log_date is not None

    def check_daily_rotation(self) -> None:
        """
        Rotate the session log when the market crosses into a new trading day (#476).

        On the first pass: set the initial date and rotate to a file named for the trading
        day (startup creates one from the wall clock, which may differ in replay mode).

        The day comes from the canonical clock and this market's anchor, not from the tick
        stamp. Two things that fixes: a forex session rolls at its swap rollover instead of
        at midnight UTC, and a feed that goes silent across the boundary no longer keeps the
        old file growing — the heartbeat advances the clock and calls this too.
        """
        if not self._run_dir or not self._writes_logs:
            return

        current_day = self._trading_day()
        if current_day is None:
            return
        day_label = current_day.strftime('%Y%m%d')

        if self._current_log_date is None:
            # First pass — ensure the file matches the trading day
            new_file_logger = create_session_file_logger(
                self._run_dir, day_label
            )
            self._logger.swap_file_logger(new_file_logger)
            self._current_log_date = day_label
            # The boot file stays: startup writes the pre-tick log (warmup bars, pipeline setup)
            # into autotrader_session.log in the run directory, whose name carries no date, so
            # neither this rotation nor the retention below reaches it — the retention matches
            # only the dated names under session_logs/.
            self._prune_rotated_session_logs(day_label)
            return

        if day_label != self._current_log_date:
            self._logger.info(
                f'📅 Date change detected: {self._current_log_date} → {day_label} — rotating session log'
            )
            new_file_logger = create_session_file_logger(
                self._run_dir, day_label
            )
            self._logger.swap_file_logger(new_file_logger)
            self._current_log_date = day_label
            self._logger.info(
                f'📅 Session log rotated to autotrader_session_{day_label}.log'
            )
            self._prune_rotated_session_logs(day_label)

    def _prune_rotated_session_logs(self, day_label: str) -> None:
        """
        Apply the configured retention to the rotated session logs (#357).

        Called on EVERY rotation, which includes the first pass — so a session that runs
        long enough to rotate is also the one that prunes, and a session that never rotates
        never deletes anything. The window is read per call rather than cached: it costs one
        dictionary lookup a day, and it is what lets an operator widen the window on a
        running deployment by editing the config before the next restart.

        Args:
            day_label: The trading day just rotated to (YYYYMMDD) — never deleted
        """
        if not self._run_dir or not self._writes_logs:
            return
        retention_days = (AppConfigManager().get_file_logging_config_object()
                          .session_logs.retention_days)
        prune_rotated_session_logs(
            self._run_dir, day_label, retention_days, self._logger)


def create_session_file_logger(run_dir: Path, date_suffix: str) -> FileLogger:
    """
    Create a new FileLogger for a specific day's session log.

    Used for daily rotation: when the trading day changes, the rotator calls this to get a
    fresh FileLogger for the new day.

    Args:
        run_dir: Session run directory (contains session_logs/ subdir)
        date_suffix: Date string for filename (YYYYMMDD)

    Returns:
        FileLogger writing to session_logs/autotrader_session_YYYYMMDD.log
    """
    path = RunEntryWriter(run_dir, RUN_TYPE_AUTOTRADER).log_path(
        RunEntryMemberId.SESSION_DAY_LOG, day=date_suffix)
    create_directory(path.parent, holds="the session's daily logs")
    return FileLogger(
        log_filename=path.name,
        file_path=path.parent,
        # The threshold the session logger actually gates on, read from config (§28) — not
        # carried over from the file logger being replaced, which never enforced it anyway.
        log_level=AppConfigManager().get_file_logging_config_object().scenario_log_level
    )
