"""
FiniexTestingIDE - Git Info Utilities

Single source of truth for reading version-control state from the working tree.
Used by run reports, certificates and performance snapshots so the git lookup
lives in ONE place instead of being re-derived per consumer.
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from python.framework.types.git_info_types import GitInfo


def get_git_commit() -> Optional[str]:
    """
    Get the current short git commit hash.

    Returns:
        Short commit hash, or None if git is unavailable or not in a repo
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _drop_untracked_under(status_lines: List[str], directory: str) -> List[str]:
    """
    Remove untracked entries that live under one directory.

    Exists for a self-inflicted wound: a certificate run writes its artifact into its
    reports directory, so the very next run reads its own output as an uncommitted change
    and reports the tree dirty although every line of code is committed. Only UNTRACKED
    entries are dropped — a modified tracked file under the same directory is a real
    change and still counts.

    Args:
        status_lines: Porcelain lines as git printed them
        directory: Directory whose untracked entries do not count

    Returns:
        The lines that remain
    """
    prefix = Path(directory).as_posix().rstrip('/') + '/'
    kept = []
    for line in status_lines:
        path = line[3:].strip().strip('"')
        if line.startswith('??') and path.startswith(prefix):
            continue
        kept.append(line)
    return kept


def get_git_info(ignore_untracked_under: Optional[str] = None) -> Optional[GitInfo]:
    """
    Get full git repository information (branch, commit, date, message, dirty).

    Args:
        ignore_untracked_under: Directory whose untracked files do not make the tree
            dirty; a certificate passes its own reports directory so it is not dirtied
            by the artifact of the previous run

    Returns:
        GitInfo with the working-tree state, or None if git is unavailable
        or not in a git repo
    """
    try:
        # Check if git is available
        subprocess.run(
            ['git', '--version'],
            capture_output=True,
            check=True,
            timeout=5
        )

        commit = get_git_commit()
        if commit is None:
            return None

        # Get branch
        branch = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        ).stdout.strip()

        # Get commit date (UTC)
        commit_date_str = subprocess.run(
            ['git', 'log', '-1', '--format=%cI'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        ).stdout.strip()
        commit_date = datetime.fromisoformat(
            commit_date_str).astimezone(timezone.utc)

        # Get commit message (first line only)
        commit_message = subprocess.run(
            ['git', 'log', '-1', '--format=%s'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        ).stdout.strip()

        # Check for uncommitted changes
        status = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        ).stdout.strip()
        status_lines = status.splitlines()
        if ignore_untracked_under:
            status_lines = _drop_untracked_under(status_lines, ignore_untracked_under)

        return GitInfo(
            branch=branch,
            commit=commit,
            date=commit_date,
            message=commit_message,
            dirty=bool(status_lines),
            uncommitted_count=len(status_lines)
        )

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # Git not available or not in a git repo - not critical
        return None
