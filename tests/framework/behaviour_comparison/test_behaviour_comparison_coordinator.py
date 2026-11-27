"""
The behaviour comparison's steps (#641) on throwaway repositories: what it refuses before it
builds anything, and a change to documents and tests only, which ends it before it builds.
"""

import json
from pathlib import Path

import pytest

from python.framework.behaviour_comparison.behaviour_comparison_coordinator import (
    BehaviourComparisonCoordinator,
)
from python.framework.behaviour_comparison.stand_home_builder import StandHomeBuilder
from python.framework.types.behaviour_comparison_types import (
    ComparedRepository,
    ComparisonEnding,
    ComparisonStep,
)
from python.framework.utils.git_info_utils import clear_git_caches
from tests.shared.git_test_repos import commit_all, make_repo, run_git, write_files


@pytest.fixture(autouse=True)
def _scratch_and_caches(tmp_path, monkeypatch):
    scratch = tmp_path / 'tmp'
    scratch.mkdir()
    monkeypatch.setattr('tempfile.tempdir', str(scratch))
    clear_git_caches()
    yield scratch
    clear_git_caches()


@pytest.fixture
def scenario_set(tmp_path) -> Path:
    path = tmp_path / 'set.json'
    path.write_text(json.dumps({'scenario_set_name': 'x', 'run_purpose': 'fixture'}),
                    encoding='utf-8')
    return path


@pytest.fixture
def framework(tmp_path) -> Path:
    return make_repo(tmp_path / 'framework', {'python/x.py': 'x = 1\n', 'docs/x.md': 'a\n'})


def _coordinator(scenario_set, framework, old='HEAD~1', new='HEAD', **kwargs):
    return BehaviourComparisonCoordinator(scenario_set, (old, kwargs.pop('old_strategy', None)),
                                          (new, kwargs.pop('new_strategy', None)),
                                          str(framework), **kwargs)


class TestNothingToRun:

    def test_a_change_to_documents_and_tests_only_ends_before_anything_is_built(
            self, scenario_set, framework, _scratch_and_caches):
        write_files(framework, {'docs/x.md': 'b\n', 'tests/test_x.py': 'def test(): pass\n'})
        commit_all(framework)
        comparison = _coordinator(scenario_set, framework).run()
        assert comparison.ending is ComparisonEnding.NO_EXECUTABLE_CHANGE
        assert comparison.ended_in_step is ComparisonStep.PATHS and comparison.cleaned_up
        paths, = comparison.changed_paths
        assert paths.repository is ComparedRepository.FRAMEWORK
        assert (paths.executable, paths.not_executed_kinds) == ([], ['docs 1', 'tests 1'])
        assert list(_scratch_and_caches.iterdir()) == []


    def test_a_change_to_comments_only_ends_before_anything_is_built(
            self, scenario_set, framework, _scratch_and_caches):
        write_files(framework, {'python/x.py': '# the first value\nx = 1\n'})
        commit_all(framework)
        comparison = _coordinator(scenario_set, framework).run()
        assert comparison.ending is ComparisonEnding.NO_EXECUTABLE_CHANGE
        paths, = comparison.changed_paths
        assert (paths.executable, paths.comment_only) == ([], ['python/x.py'])
        assert list(_scratch_and_caches.iterdir()) == []


class TestTheCleanUp:

    def test_a_step_that_raises_still_leaves_nothing_behind(self, scenario_set, framework,
                                                            _scratch_and_caches, monkeypatch):
        write_files(framework, {'python/x.py': 'x = 2\n'})
        commit_all(framework)

        def build_then_fail(self, run, stand_dir):
            (stand_dir / 'half_built').write_text('x', encoding='utf-8')
            raise RuntimeError('a step failed')

        monkeypatch.setattr(StandHomeBuilder, 'build', build_then_fail)
        coordinator = _coordinator(scenario_set, framework)
        with pytest.raises(RuntimeError):
            coordinator.run()
        assert list(_scratch_and_caches.iterdir()) == []


class TestRefusedBeforeBuilding:

    def test_an_unknown_revision_is_refused(self, scenario_set, framework):
        comparison = _coordinator(scenario_set, framework, old='no-such-branch').run()
        assert comparison.ending is ComparisonEnding.REFUSED
        assert 'no-such-branch names no commit' in comparison.ended_because

    def test_a_missing_scenario_set_is_refused(self, tmp_path, framework):
        write_files(framework, {'python/x.py': 'x = 2\n'})
        commit_all(framework)
        comparison = _coordinator(tmp_path / 'missing.json', framework).run()
        assert comparison.ending is ComparisonEnding.REFUSED
        assert 'no scenario set' in comparison.ended_because

    def test_a_strategy_revision_without_a_repository_is_refused(self, scenario_set, framework):
        write_files(framework, {'python/x.py': 'x = 2\n'})
        commit_all(framework)
        comparison = _coordinator(scenario_set, framework, old_strategy='HEAD',
                                  new_strategy='HEAD').run()
        assert 'no strategy repository' in comparison.ended_because

    def test_a_strategy_revision_on_one_stand_only_is_refused(self, scenario_set, framework,
                                                              tmp_path):
        strategy = make_repo(tmp_path / 'strategy', {'s.py': 'x = 1\n'})
        write_files(framework, {'python/x.py': 'x = 2\n'})
        commit_all(framework)
        comparison = _coordinator(scenario_set, framework, old_strategy='HEAD',
                                  strategy_root=str(strategy)).run()
        assert comparison.ending is ComparisonEnding.REFUSED
        assert 'one stand only' in comparison.ended_because

    def test_a_refusal_builds_nothing(self, scenario_set, framework, _scratch_and_caches):
        _coordinator(scenario_set, framework, old=run_git(framework, 'rev-parse', 'HEAD'),
                     new='nope').run()
        assert list(_scratch_and_caches.iterdir()) == []
