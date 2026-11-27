"""
The behaviour comparison as the console prints it (#641): one line per step it reached, and where
and why it ended. Formats only — every figure is the model's.
"""

from typing import List, Optional, Tuple

from python.framework.types.behaviour_comparison_types import (
    BehaviourComparison,
    ChangedPaths,
    ComparisonStandRun,
    ComparisonVerdict,
    DifferingField,
    ResultLine,
    StandCommits,
)

# How many characters of a commit hash the console shows
_SHORT_COMMIT = 12
# How many characters of a differing value the console shows
_SHORT_VALUE = 60
# How many differing fields of one kind of record the console lists before it says how many more
_LISTED_FIELDS = 8
# Where a result line's text starts, after its verdict
_VERDICT_WIDTH = 10
_INDENT = ' ' * 10
# Where a differing field's line starts: under the kind of record it belongs to
_FIELD_INDENT = ' ' * (_VERDICT_WIDTH + 1)


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
        lines.append(('3 run     ' if index == 0 else _INDENT) + _run(run))
    if comparison.records:
        lines.append('4 compare the two runs\' records, kind by kind, value by value')
    if comparison.second_run is not None:
        lines.append(f'{_INDENT}a value differs, so the new stand ran again: '
                     + _run(comparison.second_run))
    for index, line in enumerate(_result(comparison.result_lines)):
        lines.append(('6 result  ' if index == 0 else _INDENT) + line)
    lines.append(f'{_INDENT}ended in {comparison.ended_in_step.value}: {comparison.ended_because}')
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
    text = f'{paths.repository.value}: {changed} changed, {len(paths.executable)} executable'
    if paths.comment_only:
        text += f', {len(paths.comment_only)} comment only'
    return text


def _result(result_lines: List[ResultLine]) -> List[str]:
    """
    The result, one line per verdict and kind of record; the identical kinds share one line.

    Args:
        result_lines: The result's lines, in their order

    Returns:
        The printed lines
    """
    printed: List[str] = []
    identical = [line.words for line in result_lines
                 if line.verdict is ComparisonVerdict.IDENTICAL]
    identical_printed = False
    for line in result_lines:
        if line.verdict is ComparisonVerdict.IDENTICAL:
            if not identical_printed:
                printed.append(_verdict(line.verdict) + ' · '.join(identical))
                identical_printed = True
            continue
        if line.verdict is ComparisonVerdict.RESHAPED:
            printed.append(f'{_verdict(line.verdict)}{line.words}: {" · ".join(line.reshaped)}')
            continue
        head = f'{_verdict(line.verdict)}{line.words}'
        printed.append(f'{head}: {line.because}' if line.because else head)
        printed.extend(_fields(line.differing_fields))
        printed.extend(f'{_FIELD_INDENT}{part}' for part in line.reshaped)
    return printed


def _fields(fields: Tuple[DifferingField, ...]) -> List[str]:
    """
    A kind of record's differing fields, one line each with its first instance.

    Args:
        fields: The fields

    Returns:
        The printed lines, at most `_LISTED_FIELDS` and a line saying how many more
    """
    printed = [f'{_FIELD_INDENT}{_field(field)}' for field in fields[:_LISTED_FIELDS]]
    if len(fields) > _LISTED_FIELDS:
        printed.append(f'{_FIELD_INDENT}… and {len(fields) - _LISTED_FIELDS} more fields')
    return printed


def _field(field: DifferingField) -> str:
    """
    One differing field.

    Args:
        field: The field

    Returns:
        Its path, how often it differs, and its first instance's two values
    """
    first = field.first
    return (f'{field.folded_path} ×{field.count} — first {first.path} '
            f'{_value(first.old_value)} → {_value(first.new_value)}')


def _value(value: Optional[str]) -> str:
    """
    A differing value as the console shows it.

    Args:
        value: The value as JSON; None where the run has none there

    Returns:
        The value, shortened; '(none)' where there is none
    """
    if value is None:
        return '(none)'
    return value if len(value) <= _SHORT_VALUE else value[:_SHORT_VALUE - 1] + '…'


def _verdict(verdict: ComparisonVerdict) -> str:
    """
    A verdict, padded so the lines' texts align.

    Args:
        verdict: The verdict

    Returns:
        Its name, upper case, padded
    """
    return f'{verdict.value.upper():<{_VERDICT_WIDTH}} '


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
