"""
The behaviour comparison (#641): two code stands — a framework commit and a strategy commit
each — run on the same fixed backtest, step by step, and what each step found.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import List, Optional, Tuple

from python.framework.types.store_member_types import RunEntryMemberId


class ComparisonStand(StrEnum):
    """
    Which of the two code stands.
    """
    OLD = 'old'
    NEW = 'new'


class ComparedRepository(StrEnum):
    """
    Which repository of a stand.
    """
    FRAMEWORK = 'framework'
    STRATEGY = 'strategy'

    def has_declared_layout(self) -> bool:
        """
        Whether the declared rule of what a run does not execute applies to this repository.

        Returns:
            True for the framework, whose layout the rule is written for and held to by a
            completeness test; False for a strategy repository, whose layout nobody declared — so
            every one of its changed paths counts as executable
        """
        return self is ComparedRepository.FRAMEWORK


class ComparisonStep(StrEnum):
    """
    The steps of a comparison, in their order.
    """
    PATHS = 'paths'
    BUILD = 'build'
    RUN = 'run'
    COMPARE = 'compare'
    REACH = 'reach'
    RESULT = 'result'
    CLEAN_UP = 'clean_up'


class ComparisonEnding(StrEnum):
    """
    How a comparison ended.
    """
    NO_EXECUTABLE_CHANGE = 'no_executable_change'
    COMPARED = 'compared'
    REFUSED = 'refused'
    STAND_FAILED = 'stand_failed'

    def is_intended(self) -> bool:
        """
        Whether the comparison ended the way it is meant to end.

        Returns:
            True for nothing to run and for two runs compared to their result, whatever the
            result says; False for a refusal and a failed stand
        """
        return self in (ComparisonEnding.NO_EXECUTABLE_CHANGE, ComparisonEnding.COMPARED)


class ComparisonVerdict(StrEnum):
    """
    What a comparison found, for one kind of record and for the whole.

    IDENTICAL: both stands wrote the same values, to the last digit — the A/A's word for the same
    DIFFERENT: a value both stands write differs, and the new stand's second run agrees with its
        first
    RESHAPED: no value differs, but the records' shape does — a field, a record kind or a schema
        version only one stand writes
    UNSTABLE: the new stand's two runs disagree — the new code does not reproduce itself there,
        so what it would do after the deploy cannot be told from one run
    UNSEEN: what this comparison cannot say — a kind of record empty in both runs. Never decides
        the whole
    """
    IDENTICAL = 'identical'
    RESHAPED = 'reshaped'
    UNSTABLE = 'unstable'
    DIFFERENT = 'different'
    UNSEEN = 'unseen'

    def ranks_above(self, other: 'ComparisonVerdict') -> bool:
        """
        Whether this verdict outweighs another when the whole is judged.

        Args:
            other: The verdict held so far

        Returns:
            True where this one decides the whole instead: DIFFERENT over UNSTABLE over RESHAPED
            over IDENTICAL; UNSEEN never does
        """
        return _VERDICT_RANK[self] > _VERDICT_RANK[other]

    def summary(self) -> str:
        """
        What the whole means for the deploy, in a sentence.

        Returns:
            The sentence a comparison that ended on this verdict ends with
        """
        return _VERDICT_SUMMARY[self]


# How the verdicts weigh against each other for the whole; UNSEEN weighs nothing
_VERDICT_RANK = {ComparisonVerdict.UNSEEN: 0, ComparisonVerdict.IDENTICAL: 1,
                 ComparisonVerdict.RESHAPED: 2, ComparisonVerdict.UNSTABLE: 3,
                 ComparisonVerdict.DIFFERENT: 4}

_VERDICT_SUMMARY = {
    ComparisonVerdict.IDENTICAL: 'both stands computed the same, value by value',
    ComparisonVerdict.RESHAPED: 'the same values; the shape of the records changed',
    ComparisonVerdict.UNSTABLE: 'the new code does not reproduce itself — read the unstable values '
                                'before the deploy',
    ComparisonVerdict.DIFFERENT: 'the behaviour differs — read the cause before the deploy',
    ComparisonVerdict.UNSEEN: 'nothing was compared',
}


@dataclass(frozen=True)
class StandCommits:
    """
    One code stand: the framework commit and the strategy commit it runs.

    Args:
        framework_commit: The framework's full commit hash
        strategy_commit: The strategy repository's full commit hash; None where no strategy
            repository takes part
    """
    framework_commit: str
    strategy_commit: Optional[str] = None


@dataclass
class ChangedPaths:
    """
    One repository's changed paths between the two stands, sorted by whether a run executes them.

    Args:
        repository: Which repository
        executable: The paths a run may execute
        not_executed: The paths no run executes (documents, tests)
        not_executed_kinds: What those are, one 'kind count' entry per first directory
        changed_path_count: How many paths changed, both groups together
        comment_only: The Python files among the not-executed ones whose syntax tree is unchanged —
            a change to comments, blank lines or formatting; counted as one kind of its own
    """
    repository: ComparedRepository
    executable: List[str] = field(default_factory=list)
    not_executed: List[str] = field(default_factory=list)
    not_executed_kinds: List[str] = field(default_factory=list)
    changed_path_count: int = 0
    comment_only: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ComparedRecord:
    """
    One kind of record the comparison holds the two stands' runs against each other on.

    Args:
        member_id: The entry member that holds it in a run's directory
        words: What a result line calls it
        records_key: The list whose emptiness in both runs means nothing of the kind happened; ''
            for a stream, whose records are the list, and None where emptiness says nothing
        unseen_words: What an empty one leaves unsaid, for its UNSEEN line
        ignored_fields: Field names, at any depth of it, that differ between two runs of one
            stand and are therefore never compared
    """
    member_id: RunEntryMemberId
    words: str
    records_key: Optional[str] = None
    unseen_words: str = ''
    ignored_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RecordDifference:
    """
    One value two runs' records disagree on.

    Args:
        path: Where it stands, list positions included — `orders[0].lots`
        folded_path: The same with every position folded — `orders[*].lots`, the path differences
            are counted by
        old_value: The first run's value as JSON; None where that run has no value there
        new_value: The second run's, the same way
    """
    path: str
    folded_path: str
    old_value: Optional[str]
    new_value: Optional[str]


@dataclass
class RecordComparison:
    """
    One kind of record, held between two runs.

    Args:
        record: The kind of record
        present_in_old: Whether the first run wrote it
        present_in_new: Whether the second run wrote it
        empty_in_both: Whether its records list is empty in both runs
        differences: Every value the two disagree on, in document order
        old_only_fields: Folded paths only the first run writes
        new_only_fields: Folded paths only the second run writes
        shape_differences: Schema versions the two disagree on
    """
    record: ComparedRecord
    present_in_old: bool = True
    present_in_new: bool = True
    empty_in_both: bool = False
    differences: List[RecordDifference] = field(default_factory=list)
    old_only_fields: List[str] = field(default_factory=list)
    new_only_fields: List[str] = field(default_factory=list)
    shape_differences: List[RecordDifference] = field(default_factory=list)


@dataclass(frozen=True)
class DifferingField:
    """
    One folded path of one kind of record, with how often it differs and its first instance.

    Args:
        folded_path: The path, positions folded
        count: How many values at it differ
        first: The first of them
    """
    folded_path: str
    count: int
    first: RecordDifference


@dataclass(frozen=True)
class ResultLine:
    """
    One line of a comparison's result: a verdict for one kind of record, and what it rests on.

    Args:
        verdict: The verdict
        words: The kind of record, as the line names it
        differing_fields: For DIFFERENT, every folded path that differs, in document order; for
            UNSTABLE, the paths the new stand's two runs disagree on
        reshaped: For RESHAPED, what only one stand writes, each with the stand that writes it;
            for UNSTABLE, what only one of the new stand's two runs writes
        because: For UNSEEN and UNSTABLE, why, in a sentence
    """
    verdict: ComparisonVerdict
    words: str
    differing_fields: Tuple[DifferingField, ...] = ()
    reshaped: Tuple[str, ...] = ()
    because: str = ''


@dataclass
class ComparisonStandRun:
    """
    One stand's run: where it was built, what it ran, and which code it proved to have loaded.

    Args:
        stand: Which stand
        commits: The commits it was to run
        tree: Its code tree
        home: Its scratch data home
        run_id: Its run; None where it did not run or its run was not found
        loaded_framework_commit: The framework commit its run header names; None where unknown
        loaded_strategy_commit: The strategy commit its run header names; None where unknown or
            no strategy repository takes part
        run_seconds: How long its backtest took; None where it did not run
        failed_because: Why it failed, in a sentence; '' where it ran on its own code
    """
    stand: ComparisonStand
    commits: StandCommits
    tree: str = ''
    home: str = ''
    run_id: Optional[str] = None
    loaded_framework_commit: Optional[str] = None
    loaded_strategy_commit: Optional[str] = None
    run_seconds: Optional[float] = None
    failed_because: str = ''


@dataclass
class BehaviourComparison:
    """
    One comparison of two code stands on a fixed backtest.

    Args:
        comparison_id: The comparison's id, from the instant it started
        scenario_set: The scenario set both stands run, as given
        old: The stand that runs now
        new: The stand that is to be deployed
        changed_paths: Each taking-part repository's changed paths
        runs: The stands' runs, old first
        build_seconds: How long building both stands took; None where nothing was built
        records: Each kind of record, held between the old and the new stand's run
        second_run: The new stand's second run, made where a value differed; None otherwise
        second_run_disagrees: The new stand's two runs held against each other, kind by kind; a
            path they disagree on is never judged
        result_lines: The result, one line per verdict and kind of record, DIFFERENT first
        verdict: The whole; None where the comparison ended before its result
        ended_in_step: The step the comparison ended in
        ending: How it ended
        ended_because: Why, in a sentence
        cleaned_up: Whether its scratch folder is gone
    """
    comparison_id: str
    scenario_set: str
    old: StandCommits
    new: StandCommits
    changed_paths: List[ChangedPaths] = field(default_factory=list)
    runs: List[ComparisonStandRun] = field(default_factory=list)
    build_seconds: Optional[float] = None
    records: List[RecordComparison] = field(default_factory=list)
    second_run: Optional[ComparisonStandRun] = None
    second_run_disagrees: List[RecordComparison] = field(default_factory=list)
    result_lines: List[ResultLine] = field(default_factory=list)
    verdict: Optional[ComparisonVerdict] = None
    ended_in_step: ComparisonStep = ComparisonStep.PATHS
    ending: ComparisonEnding = ComparisonEnding.REFUSED
    ended_because: str = ''
    cleaned_up: bool = False
