"""
The behaviour comparison (#641): the same fixed backtest run on two code stands — what runs now
and what is to be deployed — step by step, and everything it built removed again.

1 paths    which paths changed between the stands; only documents, tests and comments → nothing
           to run
2 build    per stand a tree at its framework commit and a scratch data home holding its strategy
           repository at its strategy commit
3 run      old, then new, each a process of its own; each run's header proves which code it loaded
4 compare  the two runs' records, kind by kind, value by value; where a value differs, the new
           stand runs once more, and what its two runs disagree on is not judged
6 result   a verdict for each kind of record, and the whole
7 clean    the scratch folder removed, whatever happened before

The reach of the runs — which changed lines no run executed — is a later part of the issue. A
commit says that code changed; only the runs say whether what the strategy does changed.
"""

import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.data_home import relative_in_data_home
from python.framework.behaviour_comparison.changed_path_classifier import (
    classify_changed_paths,
    same_python_code,
)
from python.framework.behaviour_comparison.run_record_comparison import compare_run_records
from python.framework.behaviour_comparison.stand_home_builder import StandHomeBuilder
from python.framework.constants.behaviour_comparison_constants import (
    COMPARISON_ID_FORMAT,
    SCENARIO_SET_NAME_PATTERN,
    SCRATCH_DIR_PATTERN,
    SECOND_RUN_LABEL,
)
from python.framework.reporting.divergence_report_coordinator import newest_run_named
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.types.behaviour_comparison_types import (
    BehaviourComparison,
    ChangedPaths,
    ComparedRepository,
    ComparisonEnding,
    ComparisonStand,
    ComparisonStandRun,
    ComparisonStep,
    StandCommits,
)
from python.framework.utils.file_utils import create_directory
from python.framework.utils.git_info_utils import (
    changed_paths_between,
    get_framework_root,
    read_files_at_commits,
    resolve_commit,
)
from python.framework.utils.tree_backtest_utils import (
    RAN_EXIT_CODES,
    error_lines,
    run_backtest_in_tree,
)
from python.framework.validators.behaviour_comparison_validator import (
    judge_loaded_code,
    judge_result,
)

# What every stand's backtest runs with: no bytecode written into its trees, so the strategy
# repository reads as committed to the run's own code identity
_STAND_ENVIRONMENT = {'PYTHONDONTWRITEBYTECODE': '1'}


class BehaviourComparisonCoordinator:
    """
    Runs one behaviour comparison.

    Args:
        scenario_set: The scenario set both stands run
        old_revisions: The stand that runs now — (framework revision, strategy revision or None)
        new_revisions: The stand that is to be deployed, in the same form
        framework_root: The framework repository; None for the repository this code runs from
        strategy_root: The strategy repository; None where the stands name no strategy revision
        extra_environment: Variables added to each stand's backtest, such as a scratch HOME in a
            test; None adds nothing
    """

    def __init__(self, scenario_set: Path,
                 old_revisions: Tuple[str, Optional[str]],
                 new_revisions: Tuple[str, Optional[str]],
                 framework_root: Optional[str] = None, strategy_root: Optional[str] = None,
                 extra_environment: Optional[Dict[str, str]] = None):
        self._scenario_set = scenario_set
        self._old_revisions = old_revisions
        self._new_revisions = new_revisions
        self._framework_root = framework_root or get_framework_root() or ''
        self._strategy_root = strategy_root
        self._environment = {**_STAND_ENVIRONMENT, **(extra_environment or {})}

    def run(self) -> BehaviourComparison:
        """
        The comparison, from the revisions to its clean-up.

        Returns:
            What each step found, and where and why it ended
        """
        comparison_id = datetime.now(timezone.utc).strftime(COMPARISON_ID_FORMAT)
        refusal, old, new = self._resolve()
        comparison = BehaviourComparison(
            comparison_id=comparison_id, scenario_set=str(self._scenario_set),
            old=old or StandCommits(self._old_revisions[0]),
            new=new or StandCommits(self._new_revisions[0]))
        if refusal:
            return _ended(comparison, ComparisonStep.PATHS, ComparisonEnding.REFUSED, refusal,
                          cleaned_up=True)
        refusal = self._paths(comparison)
        if refusal:
            return _ended(comparison, ComparisonStep.PATHS, ComparisonEnding.REFUSED, refusal,
                          cleaned_up=True)
        if not any(paths.executable for paths in comparison.changed_paths):
            return _ended(comparison, ComparisonStep.PATHS, ComparisonEnding.NO_EXECUTABLE_CHANGE,
                          'no executable path changed — nothing to run', cleaned_up=True)
        scratch = Path(tempfile.gettempdir()) / SCRATCH_DIR_PATTERN.format(
            comparison_id=comparison_id)
        try:
            return self._build_and_run(comparison, scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
            comparison.cleaned_up = not scratch.exists()

    def _resolve(self) -> Tuple[str, Optional[StandCommits], Optional[StandCommits]]:
        """
        Both stands' revisions as full commits, and the scenario set checked.

        Returns:
            (why the comparison cannot start, '' where it can; the old stand; the new stand)
        """
        if not self._framework_root:
            return ('the code this runs from is in no repository; name the framework repository'
                    ), None, None
        if not self._scenario_set.is_file():
            return f'there is no scenario set at {self._scenario_set}', None, None
        if _configured_run_index().is_absolute():
            return ('the configured run index is an absolute path, so a stand\'s scratch home '
                    'cannot hold one of its own'), None, None
        stands: List[StandCommits] = []
        for framework_revision, strategy_revision in (self._old_revisions, self._new_revisions):
            framework = resolve_commit(self._framework_root, framework_revision)
            if framework is None:
                return (f'{framework_revision} names no commit of the framework repository '
                        f'{self._framework_root}'), None, None
            strategy = None
            if strategy_revision is not None:
                if self._strategy_root is None:
                    return ('a strategy revision was given, and no strategy repository'), None, None
                strategy = resolve_commit(self._strategy_root, strategy_revision)
                if strategy is None:
                    return (f'{strategy_revision} names no commit of the strategy repository '
                            f'{self._strategy_root}'), None, None
            stands.append(StandCommits(framework_commit=framework, strategy_commit=strategy))
        old, new = stands
        if (old.strategy_commit is None) != (new.strategy_commit is None):
            return ('a strategy revision was given for one stand only — both stands name one, or '
                    'neither'), old, new
        return '', old, new

    def _paths(self, comparison: BehaviourComparison) -> str:
        """
        Step 1: each taking-part repository's changed paths, classified.

        Args:
            comparison: The comparison, its stands resolved

        Returns:
            Why the paths could not be read; '' where they were
        """
        pairs = [(ComparedRepository.FRAMEWORK, self._framework_root,
                  comparison.old.framework_commit, comparison.new.framework_commit)]
        if comparison.old.strategy_commit is not None and self._strategy_root is not None:
            pairs.append((ComparedRepository.STRATEGY, self._strategy_root,
                          comparison.old.strategy_commit, comparison.new.strategy_commit or ''))
        changed: List[ChangedPaths] = []
        for repository, root, old_commit, new_commit in pairs:
            paths = changed_paths_between(root, old_commit, new_commit)
            if paths is None:
                return f'git could not say which paths of the {repository.value} repository changed'
            comment_only = _comment_only(root, old_commit, new_commit, paths)
            if comment_only is None:
                return (f'git could not read the changed Python files of the {repository.value} '
                        f'repository')
            changed.append(classify_changed_paths(repository, paths, comment_only))
        comparison.changed_paths = changed
        return ''

    def _build_and_run(self, comparison: BehaviourComparison,
                       scratch: Path) -> BehaviourComparison:
        """
        Steps 2 and 3: both stands built, then run old first.

        Args:
            comparison: The comparison, its paths read
            scratch: Its scratch folder

        Returns:
            The comparison as it ended
        """
        builder = StandHomeBuilder(self._framework_root, self._strategy_root)
        started = time.monotonic()
        runs: List[ComparisonStandRun] = []
        for stand, commits in ((ComparisonStand.OLD, comparison.old),
                               (ComparisonStand.NEW, comparison.new)):
            stand_dir = create_directory(scratch / stand.value,
                                         'a behaviour comparison stand\'s scratch folder')
            built = builder.build(ComparisonStandRun(stand=stand, commits=commits), stand_dir)
            runs.append(built)
            if built.failed_because:
                comparison.runs = runs
                return _ended(comparison, ComparisonStep.BUILD, ComparisonEnding.REFUSED,
                              f'the {stand.value} stand could not be built: {built.failed_because}')
        comparison.build_seconds = time.monotonic() - started
        comparison.runs = [self._run_stand(comparison.comparison_id, run) for run in runs]
        failed = [run for run in comparison.runs if run.failed_because]
        if failed:
            return _ended(comparison, ComparisonStep.RUN, ComparisonEnding.STAND_FAILED,
                          f'the {failed[0].stand.value} stand {failed[0].failed_because}')
        return self._compare(comparison)

    def _compare(self, comparison: BehaviourComparison) -> BehaviourComparison:
        """
        Steps 4 and 6: the two runs' records held against each other, the new stand run once more
        where a value differs, and the result judged.

        Args:
            comparison: The comparison, both stands run on their own code

        Returns:
            The comparison as it ended
        """
        old, new = comparison.runs
        old_dir, new_dir = _run_dir(old), _run_dir(new)
        if old_dir is None or new_dir is None:
            return _ended(comparison, ComparisonStep.COMPARE, ComparisonEnding.STAND_FAILED,
                          'a stand\'s run directory is not in its run index')
        comparison.records = compare_run_records(old_dir, new_dir)
        if any(record.differences for record in comparison.records):
            again = self._run_stand(comparison.comparison_id,
                                    ComparisonStandRun(stand=ComparisonStand.NEW,
                                                       commits=new.commits, tree=new.tree,
                                                       home=new.home),
                                    label=SECOND_RUN_LABEL)
            comparison.second_run = again
            again_dir = _run_dir(again) if not again.failed_because else None
            if again_dir is None:
                return _ended(comparison, ComparisonStep.COMPARE, ComparisonEnding.STAND_FAILED,
                              f'the new stand\'s second run '
                              f'{again.failed_because or "left no run directory"}')
            comparison.second_run_disagrees = compare_run_records(new_dir, again_dir)
        comparison.verdict, comparison.result_lines = judge_result(
            comparison.records, comparison.second_run_disagrees)
        return _ended(comparison, ComparisonStep.RESULT, ComparisonEnding.COMPARED,
                      comparison.verdict.summary())

    def _run_stand(self, comparison_id: str, run: ComparisonStandRun,
                   label: Optional[str] = None) -> ComparisonStandRun:
        """
        Step 3 for one stand: its backtest, its run found, the code it loaded judged.

        Args:
            comparison_id: The comparison's id
            run: The stand, built; changed in place
            label: What its scenario set's name carries in place of the stand; None for the stand

        Returns:
            The stand as it ran
        """
        home = Path(run.home)
        set_name = SCENARIO_SET_NAME_PATTERN.format(comparison_id=comparison_id,
                                                    stand=label or run.stand.value)
        set_path = self._stand_set(home, set_name)
        started = time.monotonic()
        completed = run_backtest_in_tree(Path(run.tree), home, set_path, self._environment)
        run.run_seconds = time.monotonic() - started
        if completed.returncode not in RAN_EXIT_CODES:
            run.failed_because = (f'ended its backtest with exit code {completed.returncode}: '
                                  f'{error_lines(completed.stdout + completed.stderr)}')
            return run
        index_path = home / _configured_run_index()
        run.run_id = newest_run_named(set_name, index_path=index_path)
        if run.run_id is None:
            run.failed_because = f'wrote no run named {set_name} into its run index'
            return run
        header = RunEntryReader(run_index_path=index_path).read_header(run.run_id)
        return judge_loaded_code(header.code_identity if header is not None else None, run,
                                 self._strategy_clone(home, run))

    def _stand_set(self, home: Path, set_name: str) -> Path:
        """
        The scenario set as one stand runs it: the given set, renamed so its run is found by name.

        Args:
            home: The stand's home, where the copy is written
            set_name: The name the copy carries

        Returns:
            The copy's path
        """
        document = json.loads(self._scenario_set.read_text(encoding='utf-8'))
        document['scenario_set_name'] = set_name
        path = home / f'{set_name}.json'
        path.write_text(json.dumps(document, indent=2), encoding='utf-8')
        return path

    def _strategy_clone(self, home: Path, run: ComparisonStandRun) -> Optional[Path]:
        """
        Where the stand's strategy repository was cloned.

        Args:
            home: The stand's home
            run: The stand

        Returns:
            The clone's directory; None where no strategy repository takes part
        """
        if run.commits.strategy_commit is None or self._strategy_root is None:
            return None
        relative = relative_in_data_home(self._strategy_root)
        return home / relative if relative is not None else None


def _comment_only(root: str, old_commit: str, new_commit: str,
                  paths: List[str]) -> Optional[List[str]]:
    """
    The changed Python files whose syntax tree both commits share.

    Args:
        root: The repository
        old_commit: The old stand's commit
        new_commit: The new stand's commit
        paths: The changed paths

    Returns:
        Those files, in the order given; None where git could not read them
    """
    python_paths = [path for path in paths if path.endswith('.py')]
    texts = read_files_at_commits(root, [(commit, path) for path in python_paths
                                         for commit in (old_commit, new_commit)])
    if texts is None:
        return None
    return [path for path in python_paths
            if same_python_code(texts[(old_commit, path)], texts[(new_commit, path)])]


def _run_dir(run: ComparisonStandRun) -> Optional[Path]:
    """
    Where a stand's run wrote its records.

    Args:
        run: The stand as it ran

    Returns:
        The run's directory; None where its run is not in its home's run index
    """
    if not run.run_id:
        return None
    return RunEntryReader(run_index_path=Path(run.home) / _configured_run_index()).run_dir(
        run.run_id)


def _configured_run_index() -> Path:
    """
    The run index as the configuration writes it, before any home resolves it.

    Read from the configuration as written, not from the resolved logging block: a stand's
    process resolves that value under ITS home, while this process resolves it under its own — or,
    in the test session, has it redirected.

    Returns:
        The configured path — relative to a data home, unless the configuration names an
        absolute one
    """
    return Path(str(AppConfigManager().get_config()['file_logging']['run_index']))


def _ended(comparison: BehaviourComparison, step: ComparisonStep, ending: ComparisonEnding,
           because: str, cleaned_up: bool = False) -> BehaviourComparison:
    """
    The comparison, ended.

    Args:
        comparison: The comparison
        step: The step it ended in
        ending: How
        because: Why, in a sentence
        cleaned_up: Whether nothing of it is left — True where it built nothing

    Returns:
        The comparison
    """
    comparison.ended_in_step = step
    comparison.ending = ending
    comparison.ended_because = because
    comparison.cleaned_up = cleaned_up
    return comparison
