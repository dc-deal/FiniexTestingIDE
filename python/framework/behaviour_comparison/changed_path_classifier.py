"""
Which changed paths a backtest may execute (#641). A change confined to paths no run executes —
documents, tests — cannot move what a strategy does, so the comparison ends before it builds
anything. Pure: it sorts the paths it is handed by the declared rule.

The rule is coarse on purpose and errs towards running: every path the declaration does not name
counts as executable, a path below `tests/fixtures/` too, because shipped scenario sets load their
strategies from there. It is the framework's rule: a strategy repository's layout is nobody's
declaration, so none of its paths is skipped.
"""

from typing import Dict, List

from python.framework.constants.behaviour_comparison_constants import (
    EXECUTED_BELOW_NOT_EXECUTED,
    NOT_EXECUTED_PREFIXES,
    NOT_EXECUTED_SUFFIXES,
)
from python.framework.types.behaviour_comparison_types import ChangedPaths, ComparedRepository


def is_executable_path(path: str) -> bool:
    """
    Whether a run may execute a repository-relative path.

    Args:
        path: The path, relative to its repository's top level, with forward slashes

    Returns:
        False only for a path the declaration names as not executed
    """
    if path.startswith(EXECUTED_BELOW_NOT_EXECUTED):
        return True
    if path.endswith(NOT_EXECUTED_SUFFIXES):
        return False
    return not path.startswith(NOT_EXECUTED_PREFIXES)


def classify_changed_paths(repository: ComparedRepository, paths: List[str]) -> ChangedPaths:
    """
    One repository's changed paths, sorted into executable and not executed.

    Args:
        repository: Which repository
        paths: Its changed paths, relative to its top level

    Returns:
        The paths in both groups, each in the order given, and what the not-executed ones are;
        every path executable for a repository without a declared layout
    """
    declared = repository.has_declared_layout()
    executable = [path for path in paths if not declared or is_executable_path(path)]
    not_executed = [path for path in paths if declared and not is_executable_path(path)]
    return ChangedPaths(repository=repository, executable=executable, not_executed=not_executed,
                        not_executed_kinds=_not_executed_kinds(not_executed),
                        changed_path_count=len(paths))


def _not_executed_kinds(paths: List[str]) -> List[str]:
    """
    What the not-executed paths are, by their first directory, for a reader of the result.

    Args:
        paths: Paths no run executes

    Returns:
        One 'kind count' entry per first directory, in name order; a file at the top level counts
        under its suffix (README.md → 'md')
    """
    kinds: Dict[str, int] = {}
    for path in paths:
        head = path.split('/', 1)[0] if '/' in path else path.rsplit('.', 1)[-1]
        kinds[head] = kinds.get(head, 0) + 1
    return [f'{kind} {count}' for kind, count in sorted(kinds.items())]
