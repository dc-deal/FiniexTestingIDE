"""
FiniexTestingIDE - Git Info Types

Runtime domain type for version-control information captured for run reports,
certificates and performance snapshots.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class GitInfo:
    """Version-control state of the working tree at capture time."""
    branch: str
    commit: str
    date: datetime
    message: str
    dirty: bool
    uncommitted_count: int
