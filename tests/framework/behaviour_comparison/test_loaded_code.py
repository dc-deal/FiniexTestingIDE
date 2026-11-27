"""
Whether a stand ran on its own code (#641), judged from code identities built in place: each case
changes one thing the run header could say, and an identity that cannot say is never a match.
"""

from pathlib import Path
from typing import List, Optional

import pytest

from python.framework.types.behaviour_comparison_types import (
    BehaviourComparison,
    ComparisonEnding,
    ComparisonStand,
    ComparisonStandRun,
    ComparisonVerdict,
    StandCommits,
)
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentIdentity,
    ComponentRole,
    RepositoryState,
)
from python.framework.validators.behaviour_comparison_validator import (
    comparison_exit_code,
    judge_loaded_code,
)

_FRAMEWORK = 'a0a34a7f6edd' + '0' * 28
_STRATEGY = '4327d40' + '1' * 33


def _run(tmp_path: Path, strategy: Optional[str] = _STRATEGY) -> ComparisonStandRun:
    return ComparisonStandRun(stand=ComparisonStand.OLD, tree=str(tmp_path / 'tree'),
                              home=str(tmp_path / 'home'),
                              commits=StandCommits(_FRAMEWORK, strategy))


def _identity(tmp_path: Path, framework_commit: Optional[str] = 'a0a34a7f',
              strategy_commit: Optional[str] = '4327d40', dirty: bool = False,
              components: Optional[List[ComponentIdentity]] = None) -> CodeIdentity:
    clone = str(tmp_path / 'home' / 'user_algos')
    return CodeIdentity(
        framework=RepositoryState(root=str(tmp_path / 'tree'), commit=framework_commit),
        repositories=[RepositoryState(root=clone, commit=strategy_commit, dirty=dirty)],
        components=components if components is not None else [
            ComponentIdentity(role=ComponentRole.DECISION, name='my_strategy',
                              type='user_algos/my_algo/my_strategy.py',
                              source_path=f'{clone}/my_algo/my_strategy.py', repository=clone),
            ComponentIdentity(role=ComponentRole.WORKER, name='rsi', type='CORE/rsi',
                              source_path='python/framework/workers/core/rsi_worker.py',
                              repository=str(tmp_path / 'tree'))])


def _core_only(tmp_path: Path) -> CodeIdentity:
    core = ComponentIdentity(role=ComponentRole.DECISION, name='d', type='CORE/x',
                             source_path='python/x.py', repository=str(tmp_path / 'tree'))
    return _identity(tmp_path, components=[core]).model_copy(update={'repositories': []})


def _judged(tmp_path: Path, identity: Optional[CodeIdentity],
            strategy: Optional[str] = _STRATEGY) -> ComparisonStandRun:
    clone = tmp_path / 'home' / 'user_algos' if strategy else None
    return judge_loaded_code(identity, _run(tmp_path, strategy), clone)


class TestTheStandsOwnCode:

    def test_its_own_commits_on_its_own_trees_ran(self, tmp_path):
        run = _judged(tmp_path, _identity(tmp_path))
        assert run.failed_because == ''
        assert (run.loaded_framework_commit, run.loaded_strategy_commit) == ('a0a34a7f', '4327d40')

    def test_no_identity_and_no_framework_commit_are_failures(self, tmp_path):
        assert 'no code identity' in _judged(tmp_path, None).failed_because
        assert 'unknown' in _judged(tmp_path, _identity(tmp_path, framework_commit=None)
                                    ).failed_because

    def test_another_framework_commit_is_named(self, tmp_path):
        run = _judged(tmp_path, _identity(tmp_path, framework_commit='5e6f7a8b'))
        assert 'loaded framework 5e6f7a8b' in run.failed_because

    def test_another_strategy_commit_is_named(self, tmp_path):
        run = _judged(tmp_path, _identity(tmp_path, strategy_commit='9f9f9f9'))
        assert 'loaded strategy 9f9f9f9' in run.failed_because

    def test_a_dirty_strategy_repository_fails(self, tmp_path):
        assert 'uncommitted' in _judged(tmp_path, _identity(tmp_path, dirty=True)).failed_because

    def test_a_component_from_outside_both_repositories_fails(self, tmp_path):
        stray = ComponentIdentity(role=ComponentRole.WORKER, name='stray', type='elsewhere/w.py',
                                  source_path='/elsewhere/w.py', repository='/elsewhere')
        run = _judged(tmp_path, _identity(tmp_path, components=[stray]))
        assert 'outside both repositories' in run.failed_because

    def test_a_strategy_commit_the_run_loaded_nothing_from_fails(self, tmp_path):
        identity = _core_only(tmp_path)
        assert 'loaded nothing' in _judged(tmp_path, identity).failed_because

    def test_a_core_only_run_without_a_strategy_ran(self, tmp_path):
        assert _judged(tmp_path, _core_only(tmp_path), strategy=None).failed_because == ''


class TestTheExitCode:

    def test_intended_endings_are_zero_the_rest_two(self):
        codes = {ending: comparison_exit_code(BehaviourComparison(
            comparison_id='x', scenario_set='s', old=StandCommits('a'), new=StandCommits('b'),
            ending=ending)) for ending in ComparisonEnding}
        assert codes == {ComparisonEnding.NO_EXECUTABLE_CHANGE: 0, ComparisonEnding.COMPARED: 0,
                         ComparisonEnding.REFUSED: 2, ComparisonEnding.STAND_FAILED: 2}

    @pytest.mark.parametrize('verdict, code', [(ComparisonVerdict.IDENTICAL, 0),
                                               (ComparisonVerdict.RESHAPED, 0),
                                               (ComparisonVerdict.DIFFERENT, 3)])
    def test_a_compared_result_exits_by_its_verdict(self, verdict, code):
        comparison = BehaviourComparison(
            comparison_id='x', scenario_set='s', old=StandCommits('a'), new=StandCommits('b'),
            ending=ComparisonEnding.COMPARED, verdict=verdict)
        assert comparison_exit_code(comparison) == code
