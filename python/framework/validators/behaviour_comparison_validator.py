"""
The behaviour comparison's judgements (#641): whether a stand ran on its own code, and the exit
code a comparison ends the command with.

Whether a stand ran on its own code is judged from the code identity its run header recorded at
the start, never from what the stand was told to run.

A stand counts as run on its own code only where every piece is accounted for: the framework from
its own tree at its commit, every strategy file from that tree or from the stand's strategy
repository at its commit, nothing uncommitted and nothing unknown. An identity that cannot say is
a failure, never a match — a comparison of two runs nobody can place compares nothing.
"""

import os
from pathlib import Path
from typing import Optional

from python.framework.types.behaviour_comparison_types import (
    BehaviourComparison,
    ComparisonStandRun,
)
from python.framework.types.run_origin_types import CodeIdentity
from python.framework.utils.git_info_utils import commits_agree


def judge_loaded_code(identity: Optional[CodeIdentity], run: ComparisonStandRun,
                      strategy_clone: Optional[Path]) -> ComparisonStandRun:
    """
    The stand with the commits its run loaded, and why it failed where it did not run on its own
    code.

    Args:
        identity: The code identity the run's header recorded; None where it recorded none
        run: The stand as it ran, its tree and commits set
        strategy_clone: Where the stand's strategy repository was cloned; None where none takes part

    Returns:
        The stand with loaded_framework_commit, loaded_strategy_commit and, on a failure,
        failed_because
    """
    if identity is None:
        return _failed(run, 'its run header records no code identity, so nobody can say which '
                            'code ran')
    framework = identity.framework
    if framework is None or not framework.commit:
        return _failed(run, 'its run header names no framework commit — unknown, never a match')
    run.loaded_framework_commit = framework.commit
    tree = _real(run.tree)
    if _real(framework.root) != tree:
        return _failed(run, f'it ran the framework at {framework.root}, not its own tree {tree}')
    if not commits_agree(framework.commit, run.commits.framework_commit):
        return _failed(run, f'it loaded framework {framework.commit}, not '
                            f'{run.commits.framework_commit[:12]}')
    clone = _real(str(strategy_clone)) if strategy_clone is not None else None
    for component in identity.components:
        if component.source_path is None:
            return _failed(run, f'its {component.role.value} {component.name} could not be '
                                f'resolved at the start, so nobody can say which code it was')
        root = _real(component.repository) if component.repository else None
        if root is None or root not in (tree, clone):
            return _failed(run, f'its {component.role.value} {component.name} came from '
                                f'{component.source_path}, outside both repositories of the stand')
    if clone is not None:
        strategy = next((state for state in identity.repositories
                         if state.root and _real(state.root) == clone), None)
        if strategy is None:
            return _failed(run, 'a strategy commit was given, and the run loaded nothing from the '
                                'strategy repository')
        run.loaded_strategy_commit = strategy.commit
        if not commits_agree(strategy.commit, run.commits.strategy_commit):
            return _failed(run, f'it loaded strategy {strategy.commit}, not '
                                f'{(run.commits.strategy_commit or "")[:12]}')
    if identity.is_dirty():
        return _failed(run, 'uncommitted, unversioned or unreadable code ran, so the stand is not '
                            'the commits it names')
    return run


def comparison_exit_code(comparison: BehaviourComparison) -> int:
    """
    The exit code a comparison ends the command with.

    Args:
        comparison: The comparison

    Returns:
        0 where it ended as intended; 2 where it was refused or a stand failed
    """
    return 0 if comparison.ending.is_intended() else 2


def _real(path: Optional[str]) -> Optional[str]:
    """
    A path with its links resolved, so two spellings of one directory compare equal.

    Args:
        path: The path; None stays None

    Returns:
        The resolved path
    """
    return os.path.realpath(path) if path else None


def _failed(run: ComparisonStandRun, because: str) -> ComparisonStandRun:
    """
    A stand that did not run on its own code.

    Args:
        run: The stand
        because: Why

    Returns:
        The stand with its reason
    """
    run.failed_because = because
    return run
