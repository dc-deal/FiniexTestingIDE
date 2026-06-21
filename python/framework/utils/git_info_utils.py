"""
FiniexTestingIDE - Git Info Utilities

Single source of truth for reading version-control state from the working tree.
Used by run reports, certificates and performance snapshots so the git lookup
lives in ONE place instead of being re-derived per consumer.
"""

import subprocess
from datetime import datetime, timezone
from typing import Optional

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


def get_git_info() -> Optional[GitInfo]:
    """
    Get full git repository information (branch, commit, date, message, dirty).

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
        uncommitted_count = len(status.splitlines())

        return GitInfo(
            branch=branch,
            commit=commit,
            date=commit_date,
            message=commit_message,
            dirty=bool(status),
            uncommitted_count=uncommitted_count
        )

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # Git not available or not in a git repo - not critical
        return None
