"""
FiniexTestingIDE - Git Info Types

Runtime domain type for version-control information captured for run reports,
certificates and performance snapshots.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple


@dataclass
class GitInfo:
    """Version-control state of the working tree at capture time."""
    branch: str
    commit: str
    date: datetime
    message: str
    dirty: bool
    uncommitted_count: int


@dataclass
class RepoStatus:
    """
    One repository's state, addressed by its root rather than by the process cwd (#551).

    `get_git_info` answers for THIS repository only — the one the code is imported from. A strategy
    may live in another repository entirely — `user_algos/` is its own — and that one needs the
    same answer under its own root.

    Args:
        root: The repository's top-level directory as git reports it
        commit: Short commit hash, or None when HEAD has no commit yet
        branch: The checked-out branch as `rev-parse --abbrev-ref` names it (`HEAD` when
            detached), read in the same breath as the commit
        dirty: Whether anything tracked is modified or anything untracked is present
        status_lines: The porcelain lines behind `dirty`, as git printed them — plus one
            `!h <path>` line per entry git was told not to look at (assume-unchanged or
            skip-worktree), which is a change git cannot see rather than no change
        changed_paths: Every repository-relative path those lines name
        untracked_paths: The subset git does not track at all
        hidden_paths: The subset flagged assume-unchanged or skip-worktree
    """
    root: str
    commit: Optional[str]
    dirty: bool
    branch: Optional[str] = None
    status_lines: List[str] = field(default_factory=list)
    changed_paths: List[str] = field(default_factory=list)
    untracked_paths: List[str] = field(default_factory=list)
    hidden_paths: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RepoCommitHistory:
    """
    What one repository's history says about its HEAD (#559). Two reads that fail apart, so one
    field can be unknown while the other is known.

    Args:
        subject: The HEAD commit's subject line, None when it could not be read
        recent_commits: The commits on HEAD's first-parent line, newest first, each as a
            fixed-length prefix of its full hash; None when they could not be read. A shallow
            clone lists only the commits it holds
    """
    subject: Optional[str] = None
    recent_commits: Optional[Tuple[str, ...]] = None
