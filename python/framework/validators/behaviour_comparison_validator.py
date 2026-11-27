"""
The behaviour comparison's judgements (#641): whether a stand ran on its own code, what the two
runs' records say for each kind of record and for the whole, and the exit code a comparison ends
the command with.

Whether a stand ran on its own code is judged from the code identity its run header recorded at
the start, never from what the stand was told to run.

A stand counts as run on its own code only where every piece is accounted for: the framework from
its own tree at its commit, every strategy file from that tree or from the stand's strategy
repository at its commit, nothing uncommitted and nothing unknown. An identity that cannot say is
a failure, never a match — a comparison of two runs nobody can place compares nothing.

The result judges each kind of record: DIFFERENT where a value both stands write differs and the
new stand's second run agrees with its first there, RESHAPED where only the records' shape moved,
IDENTICAL where everything agrees, UNSEEN where the comparison cannot say. A value the new stand's
two runs disagree on is never judged: it is shown as UNSEEN, never dropped.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from python.framework.behaviour_comparison.run_record_comparison import disagreeing_paths
from python.framework.types.behaviour_comparison_types import (
    BehaviourComparison,
    ComparisonStandRun,
    ComparisonVerdict,
    DifferingField,
    RecordComparison,
    RecordDifference,
    ResultLine,
)
from python.framework.types.run_origin_types import CodeIdentity
from python.framework.utils.git_info_utils import commits_agree

# The order a result's lines stand in: what needs a reading first
_LINE_ORDER = (ComparisonVerdict.DIFFERENT, ComparisonVerdict.RESHAPED,
               ComparisonVerdict.IDENTICAL, ComparisonVerdict.UNSEEN)


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


def judge_result(records: Sequence[RecordComparison],
                 second_run: Sequence[RecordComparison]
                 ) -> Tuple[ComparisonVerdict, List[ResultLine]]:
    """
    The result of a comparison: a verdict for each kind of record, and the whole.

    Args:
        records: The old and the new stand's run, held kind by kind
        second_run: The new stand's two runs held against each other; empty where it ran once

    Returns:
        (the whole — the weightiest line's verdict, UNSEEN where nothing was compared; the lines,
        DIFFERENT first, then RESHAPED, IDENTICAL and UNSEEN)
    """
    second_by_member = {second.record.member_id: second for second in second_run}
    lines: List[ResultLine] = []
    for record in records:
        lines.extend(_judge_record(record, second_by_member.get(record.record.member_id)))
    whole = ComparisonVerdict.UNSEEN
    for line in lines:
        if line.verdict.ranks_above(whole):
            whole = line.verdict
    return whole, sorted(lines, key=lambda line: _LINE_ORDER.index(line.verdict))


def comparison_exit_code(comparison: BehaviourComparison) -> int:
    """
    The exit code a comparison ends the command with.

    Args:
        comparison: The comparison

    Returns:
        3 where its result is DIFFERENT; 0 where it otherwise ended as intended; 2 where it was
        refused or a stand failed
    """
    if comparison.verdict is ComparisonVerdict.DIFFERENT:
        return 3
    return 0 if comparison.ending.is_intended() else 2


def _judge_record(record: RecordComparison,
                  second: Optional[RecordComparison]) -> List[ResultLine]:
    """
    The lines one kind of record earns.

    Args:
        record: The kind of record, held between the two stands
        second: The same kind held between the new stand's two runs; None where it ran once

    Returns:
        Its verdict lines — DIFFERENT and RESHAPED both where both apply, none where neither run
        wrote it — and an UNSEEN line naming everything the new stand's two runs disagree on
    """
    words = record.record.words
    noise = _second_run_line(second, words)
    if not record.present_in_old and not record.present_in_new and not record.empty_in_both:
        return noise
    not_judged: Set[str] = set(disagreeing_paths([second]).get(
        record.record.member_id.value, [])) if second is not None else set()
    lines: List[ResultLine] = []
    # '' stands for the whole record: the second run wrote it where the first did not
    whole_unjudged = '' in not_judged
    unjudged: List[RecordDifference] = []
    judged: List[RecordDifference] = []
    for difference in record.differences:
        if whole_unjudged or difference.folded_path in not_judged:
            unjudged.append(difference)
        else:
            judged.append(difference)
    reshaped = _reshaped(record, not_judged)
    if judged:
        lines.append(ResultLine(ComparisonVerdict.DIFFERENT, words,
                                differing_fields=_fields(judged)))
    if reshaped:
        lines.append(ResultLine(ComparisonVerdict.RESHAPED, words, reshaped=tuple(reshaped)))
    if not judged and not reshaped:
        if record.empty_in_both:
            lines.append(ResultLine(
                ComparisonVerdict.UNSEEN, words,
                unseen_because=f'none in either run — says nothing about '
                               f'{record.record.unseen_words or words}'))
        elif not unjudged:
            lines.append(ResultLine(ComparisonVerdict.IDENTICAL, words))
    return lines + noise


def _second_run_line(second: Optional[RecordComparison], words: str) -> List[ResultLine]:
    """
    What the new stand's two runs disagree on for one kind of record — every path, whether or
    not the old and the new stand differ there.

    Args:
        second: The kind of record held between the new stand's two runs; None where it ran once
        words: The kind of record, as a line names it

    Returns:
        One UNSEEN line with the paths and the second run's values; none where the two agree
    """
    if second is None:
        return []
    one_sided = [f'a field only one of its two runs writes — {path}'
                 for path in second.old_only_fields + second.new_only_fields]
    one_sided += [f'{difference.path} {difference.old_value} → {difference.new_value}'
                  for difference in second.shape_differences]
    if second.present_in_old != second.present_in_new:
        one_sided.append('only one of its two runs writes it')
    if not second.differences and not one_sided:
        return []
    return [ResultLine(ComparisonVerdict.UNSEEN, words,
                       differing_fields=_fields(second.differences), reshaped=tuple(one_sided),
                       unseen_because='the new stand\'s two runs disagree here — not judged')]


def _reshaped(record: RecordComparison, not_judged: Set[str]) -> List[str]:
    """
    What only one stand writes of a kind of record, in a few words each.

    Args:
        record: The kind of record
        not_judged: Its folded paths the new stand's two runs disagree on

    Returns:
        One entry per one-sided part — the whole record, a field, a schema version
    """
    if record.present_in_old != record.present_in_new:
        if '' in not_judged:
            return []
        return ['only the old stand writes it' if record.present_in_old
                else 'only the new stand writes it']
    reshaped = [f'a field only the old stand writes — {path}'
                for path in record.old_only_fields if path not in not_judged]
    reshaped += [f'a field only the new stand writes — {path}'
                 for path in record.new_only_fields if path not in not_judged]
    reshaped += [f'{difference.path} {difference.old_value} → {difference.new_value}'
                 for difference in record.shape_differences
                 if difference.folded_path not in not_judged]
    return reshaped


def _fields(differences: List[RecordDifference]) -> Tuple[DifferingField, ...]:
    """
    Differences counted by their folded path, each with its first instance.

    Args:
        differences: The differences, in document order

    Returns:
        One entry per folded path, in the order each first appears
    """
    counted: Dict[str, List[RecordDifference]] = {}
    for difference in differences:
        counted.setdefault(difference.folded_path, []).append(difference)
    return tuple(DifferingField(folded_path=path, count=len(group), first=group[0])
                 for path, group in counted.items())


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
