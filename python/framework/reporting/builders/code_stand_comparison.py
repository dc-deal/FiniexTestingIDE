"""
Comparing two code stands (#559): which repositories moved between two sessions, and how far.

Pure. It reads the two stands the ledger rows keep and asks git nothing, so the answer does not
depend on when it is asked or on what is checked out by then, and it needs no clone at all.

The distance comes from where one stand's commit stands in the other stand's recorded history,
never from `rev-list --count` between the two. Measured on throwaway repositories: that command
answers 1 with exit 0 where the truth is 3 on a clone that re-fetched itself shallowly, and 0 after
a rollback. A commit found in neither history reads as unknown, so a wrong number cannot come out.
"""

from typing import List, Optional

from python.framework.types.api.report_types import CodeChangeDirection, DeploymentCodeChange
from python.framework.types.run_origin_types import CodeStand, RepositoryStand, repository_labels
from python.framework.utils.git_info_utils import commits_agree


def stands_agree(first: CodeStand, second: CodeStand) -> bool:
    """
    Whether two sessions started on the same code, in every repository.

    Args:
        first: One session's stand
        second: The other session's stand

    Returns:
        True when no repository differs between them; never where a commit is unknown
    """
    return not code_changes_between(first, second)


def code_changes_between(older: CodeStand, newer: CodeStand) -> List[DeploymentCodeChange]:
    """
    What moved from one session's code to the next one's, one entry per repository that did.

    Args:
        older: The earlier session's stand
        newer: The later session's stand

    Returns:
        The changes in the newer stand's order, then the repositories only the older stand holds;
        empty when it is the same code. Beside a stand that names nothing because nobody could read
        it, a repository reads as unknown: nobody knows the other session did not use it
    """
    labels = repository_labels(
        repository.repository for repository in older.repositories + newer.repositories)
    in_older = {repository.repository: repository for repository in older.repositories}
    in_newer = {repository.repository for repository in newer.repositories}

    changes: List[DeploymentCodeChange] = []
    for repository in newer.repositories:
        counterpart = in_older.get(repository.repository)
        if counterpart is None:
            changes.append(DeploymentCodeChange(
                repository_label=labels[repository.repository],
                new_commit=repository.commit,
                change_direction=(CodeChangeDirection.UNKNOWN if older.is_unread()
                                  else CodeChangeDirection.ADDED),
                subject=repository.subject))
        elif not _same_code(counterpart, repository):
            changes.append(_moved(labels[repository.repository], counterpart, repository))
    for repository in older.repositories:
        if repository.repository not in in_newer:
            changes.append(DeploymentCodeChange(
                repository_label=labels[repository.repository],
                old_commit=repository.commit,
                change_direction=(CodeChangeDirection.UNKNOWN if newer.is_unread()
                                  else CodeChangeDirection.REMOVED)))
    return changes


def _same_code(older: RepositoryStand, newer: RepositoryStand) -> bool:
    """
    Whether one repository's code is the same in two stands.

    Args:
        older: Its entry in the earlier stand
        newer: Its entry in the later stand

    Returns:
        True when the commits agree and so do the uncommitted changes. Never for a commit nobody
        could read, also against another one, and never for a dirty tree without a digest: both
        are unknown, and unknown is not clean
    """
    if not commits_agree(older.commit, newer.commit) or older.dirty != newer.dirty:
        return False
    if older.dirty and older.diff_hash is None:
        return False
    return older.diff_hash == newer.diff_hash


def _moved(label: str, older: RepositoryStand, newer: RepositoryStand) -> DeploymentCodeChange:
    """
    How one repository moved, when both stands hold it and its code differs.

    Args:
        label: The word a reader knows the repository by
        older: Its entry in the earlier stand
        newer: Its entry in the later stand

    Returns:
        The change. Its subject is the message of the commit that made the difference: the newer
        commit's, on a rollback the commit it went back from, and none for uncommitted changes
    """
    if commits_agree(older.commit, newer.commit):
        return DeploymentCodeChange(
            repository_label=label, old_commit=older.commit, new_commit=newer.commit,
            change_direction=CodeChangeDirection.UNCOMMITTED)
    ahead = _position_in_history(older.commit, newer.recent_commits)
    if ahead is not None:
        return DeploymentCodeChange(
            repository_label=label, old_commit=older.commit, new_commit=newer.commit,
            commits_between=ahead, change_direction=CodeChangeDirection.FORWARD, subject=newer.subject)
    behind = _position_in_history(newer.commit, older.recent_commits)
    if behind is not None:
        return DeploymentCodeChange(
            repository_label=label, old_commit=older.commit, new_commit=newer.commit,
            commits_between=behind, change_direction=CodeChangeDirection.BACK, subject=older.subject)
    return DeploymentCodeChange(
        repository_label=label, old_commit=older.commit, new_commit=newer.commit,
        change_direction=CodeChangeDirection.UNKNOWN, subject=newer.subject)


def _position_in_history(commit: Optional[str], history: Optional[List[str]]) -> Optional[int]:
    """
    How many commits before the head of a recorded history a commit stands.

    Args:
        commit: The commit to look for, None when unknown
        history: The recorded history, newest first, None when it was not read

    Returns:
        Its position, which is the distance from the head, or None when the history does not hold it
    """
    for position, recorded in enumerate(history or []):
        if commits_agree(commit, recorded):
            return position
    return None
