"""
FiniexTestingIDE - Session Log Retention (#357)

A live session rotates its log at the trading-day boundary and, until this existed, kept
every rotated day for as long as the session ran. Over a thirty-day run that is thirty files
nobody removes — the kind of pressure that arrives in the middle of the one run the project
exists to complete, not at its start.

Deliberately NOT the same question as the run TREE. That one is pruned by a CLI the operator
triggers, and it stays that way because a deletion nobody asked for is a deletion nobody can
explain. These files belong to a session that is still running, where there is no operator to
ask — so the rule is declared once, in config, and applied by the session itself.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import List

from python.framework.logging.scenario_logger import ScenarioLogger

# The rotated files, and ONLY those. Anything else in the directory is left alone: the
# retention rule was written for a known name pattern, and a pattern that also matched
# something unforeseen would delete it just as quietly.
_ROTATED_LOG_PATTERN = re.compile(r'^autotrader_session_(\d{8})\.log$')

SESSION_LOGS_SUBDIR = 'session_logs'


def prune_rotated_session_logs(
    run_dir: Path,
    current_day: str,
    retention_days: int,
    logger: ScenarioLogger,
) -> List[str]:
    """
    Remove rotated session logs older than the retention window (#357).

    The age is measured against the session's CURRENT trading day rather than against the
    wall clock, and it is read from the file NAME rather than from its mtime. Both follow
    from what the name means: it is the trading day the file holds (§47), while the mtime is
    only the last time something was written into it — and a `stat` per file costs 2.1 ms on
    this tree (§42) to answer a question the name already answers.

    A file that cannot be removed does not end the session: the remaining ones are still
    worth removing, and the failure reaches the session pot (§35) where the operator sees it.

    Args:
        run_dir: The session run directory (holds the session_logs/ subdirectory)
        current_day: The active trading day as YYYYMMDD — never a deletion candidate
        retention_days: How many rotated days to keep; 0 disables pruning entirely
        logger: Session logger — one line when something was removed (§35 pot)

    Returns:
        The names of the files that were removed, in the order they were removed
    """
    if retention_days <= 0:
        return []

    log_dir = run_dir / SESSION_LOGS_SUBDIR
    if not log_dir.is_dir():
        return []

    removed: List[str] = []
    failed: List[str] = []
    for path in sorted(log_dir.iterdir()):
        match = _ROTATED_LOG_PATTERN.match(path.name)
        if match is None:
            continue
        day = match.group(1)
        # The active file is protected by the day comparison itself — it is never older
        # than the current day — but it is stated here too, because this is the one
        # guarantee a reader of this function comes to check.
        if day >= current_day or _days_between(day, current_day) <= retention_days:
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except OSError as error:
            failed.append(f'{path.name} ({error})')

    if removed:
        logger.info(
            f'🧹 Session-log retention: removed {len(removed)} rotated log(s) older than '
            f'{retention_days} day(s) — {", ".join(removed)}')
    if failed:
        logger.warning(
            f'⚠️ Session-log retention could not remove {len(failed)} file(s): '
            f'{", ".join(failed)}')
    return removed


def _days_between(day: str, current_day: str) -> int:
    """
    Whole days from a rotated file's day to the session's current day.

    Args:
        day: The file's trading day as YYYYMMDD
        current_day: The active trading day as YYYYMMDD

    Returns:
        The difference in days, never negative for a day in the past
    """
    parsed = datetime.strptime(day, '%Y%m%d')
    current = datetime.strptime(current_day, '%Y%m%d')
    return (current - parsed).days
