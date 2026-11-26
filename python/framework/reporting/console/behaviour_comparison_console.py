"""
The behaviour comparison as the console prints it (#641): one line per step it reached, and where
and why it ended. Formats only — every figure is the model's.
"""

from typing import List

from python.framework.types.behaviour_comparison_types import (
    BehaviourComparison,
    ChangedPaths,
    ComparisonStandRun,
    StandCommits,
)

# How many characters of a commit hash the console shows
_SHORT_COMMIT = 12


def render_behaviour_comparison(comparison: BehaviourComparison) -> str:
    """
    The comparison, step by step.

    Args:
        comparison: The comparison as it ended

    Returns:
        Its lines, joined
    """
    lines = [f'Behaviour comparison {comparison.comparison_id}',
             f'  set   {comparison.scenario_set}',
             f'  old   {_stand(comparison.old)}',
             f'  new   {_stand(comparison.new)}']
    if comparison.changed_paths:
        lines.append('1 paths   '
                     + ' · '.join(_paths(paths) for paths in comparison.changed_paths))
    if comparison.build_seconds is not None:
        lines.append(f'2 build   old and new in {comparison.build_seconds:.1f} s')
    ran = [run for run in comparison.runs if run.run_seconds is not None]
    for index, run in enumerate(ran):
        lines.append(('3 run     ' if index == 0 else '          ') + _run(run))
    lines.append(f'          ended in {comparison.ended_in_step.value}: {comparison.ended_because}')
    if comparison.build_seconds is None and not comparison.runs:
        lines.append('7 clean   nothing was built')
    else:
        lines.append('7 clean   scratch removed' if comparison.cleaned_up
                     else '7 clean   ⚠️ the scratch folder could not be removed')
    return '\n'.join(lines)


def _stand(commits: StandCommits) -> str:
    """
    One stand's commits.

    Args:
        commits: The stand

    Returns:
        Its framework and strategy commit, shortened
    """
    text = f'framework {commits.framework_commit[:_SHORT_COMMIT]}'
    if commits.strategy_commit is not None:
        text += f' · strategy {commits.strategy_commit[:_SHORT_COMMIT]}'
    return text


def _paths(paths: ChangedPaths) -> str:
    """
    One repository's changed paths, counted.

    Args:
        paths: The repository's paths

    Returns:
        Its counts, and what the not-executed ones are
    """
    changed = paths.changed_path_count
    if not changed:
        return f'{paths.repository.value}: none changed'
    if not paths.executable:
        kinds: List[str] = paths.not_executed_kinds
        return (f'{paths.repository.value}: {changed} changed, none executable '
                f'({" · ".join(kinds)})')
    return f'{paths.repository.value}: {changed} changed, {len(paths.executable)} executable'


def _run(run: ComparisonStandRun) -> str:
    """
    One stand's run.

    Args:
        run: The stand as it ran

    Returns:
        Its run, the code it loaded and how long it took, or why it failed
    """
    mark = '✗' if run.failed_because else '✓'
    text = f'{run.stand.value:<4} {mark} run {run.run_id or "—"}'
    if run.loaded_framework_commit is not None:
        text += f' · loaded framework {run.loaded_framework_commit}'
    if run.loaded_strategy_commit is not None:
        text += f', strategy {run.loaded_strategy_commit}'
    text += f' · {run.run_seconds:.0f} s'
    if run.failed_because:
        text += f' — {run.failed_because}'
    return text
