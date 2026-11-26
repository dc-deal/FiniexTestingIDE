"""
The behaviour comparison (#641): two code stands — a framework commit and a strategy commit
each — run on the same fixed backtest, step by step, and what each step found.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import List, Optional


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
    STANDS_RAN = 'stands_ran'
    REFUSED = 'refused'
    STAND_FAILED = 'stand_failed'

    def is_intended(self) -> bool:
        """
        Whether the comparison ended the way it is meant to end.

        Returns:
            True for nothing to run and for both stands run on their own code; False for a refusal
            and a failed stand
        """
        return self in (ComparisonEnding.NO_EXECUTABLE_CHANGE, ComparisonEnding.STANDS_RAN)


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
    """
    repository: ComparedRepository
    executable: List[str] = field(default_factory=list)
    not_executed: List[str] = field(default_factory=list)
    not_executed_kinds: List[str] = field(default_factory=list)
    changed_path_count: int = 0


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
    ended_in_step: ComparisonStep = ComparisonStep.PATHS
    ending: ComparisonEnding = ComparisonEnding.REFUSED
    ended_because: str = ''
    cleaned_up: bool = False
