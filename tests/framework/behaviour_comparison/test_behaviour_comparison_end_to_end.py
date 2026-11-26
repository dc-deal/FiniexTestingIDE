"""
One behaviour comparison end to end (#641): a short fixture set run on two real commits of this
repository, each stand from its own throwaway tree against its own scratch home — each run proving
which code it loaded, and nothing left behind.

The newer commit is HEAD, the older one the parent of the newest commit that changed `python/`, so
the comparison always has executable paths to run. One comparison for the module: it builds two
trees and runs two backtests.
"""

from pathlib import Path
from typing import Dict, Tuple

import pytest

from python.framework.behaviour_comparison.behaviour_comparison_coordinator import (
    BehaviourComparisonCoordinator,
)
from python.framework.types.behaviour_comparison_types import (
    BehaviourComparison,
    ComparisonEnding,
    ComparisonStep,
)
from python.framework.utils.git_info_utils import commits_agree, get_framework_root
from tests.shared.git_test_repos import run_git

_SET = Path('configs') / 'scenario_sets' / 'backtesting' / 'margin_validation_test.json'


def _real_stores(root: Path) -> Dict[str, Tuple[int, int]]:
    """
    The files a stand would write into were it not isolated: the processed data's indexes and the
    run tree's index of the tree the comparison is started from.

    Args:
        root: The tree

    Returns:
        Path → (size, mtime) for each such file that exists
    """
    files = list((root / 'data' / 'processed').glob('*.parquet'))
    files += [root / 'runs' / 'runs_index.parquet']
    return {str(path): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in files if path.exists()}


@pytest.fixture(scope='module')
def scratch(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp('behaviour_scratch')


@pytest.fixture(scope='module')
def outcome(tmp_path_factory, scratch) -> Tuple[BehaviourComparison, bool]:
    root = Path(get_framework_root() or '')
    newest_python = run_git(root, 'rev-list', '-n', '1', 'HEAD', '--', 'python/')
    patch = pytest.MonkeyPatch()
    patch.setattr('tempfile.tempdir', str(scratch))
    patch.chdir(root)
    before = _real_stores(root)
    try:
        comparison = BehaviourComparisonCoordinator(
            root / _SET, (f'{newest_python}~1', None), ('HEAD', None), str(root),
            # A scratch HOME keeps the stands' backtests out of the machine's activity list
            extra_environment={'HOME': str(tmp_path_factory.mktemp('stand_home_dir'))}).run()
        yield comparison, _real_stores(root) == before
    finally:
        patch.undo()


@pytest.fixture(scope='module')
def comparison(outcome) -> BehaviourComparison:
    return outcome[0]


class TestTwoStandsRun:

    def test_both_stands_ran_on_their_own_code(self, comparison):
        assert comparison.ending is ComparisonEnding.STANDS_RAN, comparison.ended_because
        assert comparison.ended_in_step is ComparisonStep.RUN
        for run, commits in zip(comparison.runs, (comparison.old, comparison.new)):
            assert run.failed_because == '' and run.run_id
            assert commits_agree(run.loaded_framework_commit, commits.framework_commit)

    def test_the_two_stands_are_two_commits_with_executable_changes(self, comparison):
        assert comparison.old.framework_commit != comparison.new.framework_commit
        assert comparison.changed_paths[0].executable

    def test_nothing_is_left_behind(self, comparison, scratch):
        assert comparison.cleaned_up and list(scratch.iterdir()) == []

    def test_the_stores_of_the_tree_it_started_from_are_untouched(self, outcome):
        assert outcome[1]
