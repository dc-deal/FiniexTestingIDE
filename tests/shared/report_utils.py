"""
FiniexTestingIDE - Shared Test Report Utilities

Shared utilities for test report/receipt generation.
Used by live_adapters and any future adapter test suites.
"""

import subprocess
from typing import Optional


def get_git_commit() -> Optional[str]:
    """
    Get current git commit hash.

    Returns:
        Short commit hash or None if not in a git repo
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
