"""
Two behaviour comparisons end to end (#641), on a throwaway clone of this repository with two
commits of its own on top of HEAD, made in the probe the fixture set runs:

    neutral   the probe's BUY/SELL choice written the other way round — another syntax tree,
              the same decisions
    defect    every opened position sized at half its lot

HEAD against the neutral commit must come out IDENTICAL; the neutral commit against the defect
DIFFERENT, naming the lots, with the new stand's second run agreeing with its first. Each stand
runs from its own throwaway tree against its own scratch home, each run proving which code it
loaded, and nothing is left behind.

The clone is made without a checkout: only the probe is written into its working tree, and its
commits are made from an index read from HEAD, so the clone costs no read of the repository's
files. The four stands' trees are what the module spends its time on.
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
    ComparisonVerdict,
)
from python.framework.utils.git_info_utils import commits_agree, get_framework_root
from tests.shared.git_test_repos import run_git

_SET = Path('configs') / 'scenario_sets' / 'backtesting' / 'margin_validation_test.json'
_PROBE = 'python/framework/decision_logic/core/test_probes/margin_stress_probe.py'
_CHOICE = """                action = (
                    DecisionLogicAction.BUY
                    if direction == 'LONG'
                    else DecisionLogicAction.SELL
                )"""
_NEUTRAL_CHOICE = """                action = (
                    DecisionLogicAction.SELL
                    if direction != 'LONG'
                    else DecisionLogicAction.BUY
                )"""
_LOT = "lot_size = spec.get('lot_size', self.default_lot_size)\n"
_HALF_LOT = "lot_size = spec.get('lot_size', self.default_lot_size) * 0.5\n"


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


def _commit_probe(work: Path, old: str, new: str, message: str) -> str:
    """
    One commit on the clone that changes a passage of the probe, wherever it stands.

    Args:
        work: The clone, its index read from a commit
        old: The passage as it stands; every occurrence is changed, and there must be one
        new: What it becomes
        message: The commit message

    Returns:
        The commit's hash
    """
    run_git(work, 'checkout', 'HEAD', '--', _PROBE)
    probe = work / _PROBE
    text = probe.read_text(encoding='utf-8')
    assert old in text, f'the probe no longer holds the passage this test changes: {old}'
    probe.write_text(text.replace(old, new), encoding='utf-8')
    run_git(work, 'add', _PROBE)
    run_git(work, 'commit', '-q', '-m', message)
    return run_git(work, 'rev-parse', 'HEAD')


@pytest.fixture(scope='module')
def scratch(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp('behaviour_scratch')


@pytest.fixture(scope='module')
def outcome(tmp_path_factory, scratch) -> Tuple[BehaviourComparison, BehaviourComparison, bool]:
    root = Path(get_framework_root() or '')
    work = tmp_path_factory.mktemp('work') / 'framework'
    run_git(root, 'clone', '--quiet', '--shared', '--no-checkout', str(root), str(work))
    run_git(work, 'read-tree', 'HEAD')
    base = run_git(work, 'rev-parse', 'HEAD')
    neutral = _commit_probe(work, _CHOICE, _NEUTRAL_CHOICE, 'the choice written the other way')
    defect = _commit_probe(work, _LOT, _HALF_LOT, 'every position at half its lot')
    patch = pytest.MonkeyPatch()
    patch.setattr('tempfile.tempdir', str(scratch))
    patch.chdir(root)
    before = _real_stores(root)
    # A scratch HOME keeps the stands' backtests out of the machine's activity list
    environment = {'HOME': str(tmp_path_factory.mktemp('stand_home_dir'))}
    try:
        same = BehaviourComparisonCoordinator(root / _SET, (base, None), (neutral, None),
                                              str(work), extra_environment=environment).run()
        differs = BehaviourComparisonCoordinator(root / _SET, (neutral, None), (defect, None),
                                                 str(work), extra_environment=environment).run()
        yield same, differs, _real_stores(root) == before
    finally:
        patch.undo()


@pytest.fixture(scope='module')
def same(outcome) -> BehaviourComparison:
    return outcome[0]


@pytest.fixture(scope='module')
def differs(outcome) -> BehaviourComparison:
    return outcome[1]


class TestTwoStandsRun:

    def test_both_stands_ran_on_their_own_code(self, same):
        assert same.ending is ComparisonEnding.COMPARED, same.ended_because
        for run, commits in zip(same.runs, (same.old, same.new)):
            assert run.failed_because == '' and run.run_id
            assert commits_agree(run.loaded_framework_commit, commits.framework_commit)

    def test_the_neutral_commit_is_executable(self, same):
        assert same.changed_paths[0].executable == [_PROBE]

    def test_nothing_is_left_behind(self, same, differs, scratch):
        assert same.cleaned_up and differs.cleaned_up and list(scratch.iterdir()) == []

    def test_the_stores_of_the_tree_it_started_from_are_untouched(self, outcome):
        assert outcome[2]


class TestTheResult:

    def test_a_neutral_change_is_identical(self, same):
        assert same.ended_in_step is ComparisonStep.RESULT
        assert same.verdict is ComparisonVerdict.IDENTICAL, [
            (line.verdict, line.words) for line in same.result_lines]
        assert same.second_run is None

    def test_a_seeded_defect_is_different_and_names_the_lots(self, differs):
        assert differs.verdict is ComparisonVerdict.DIFFERENT, differs.ended_because
        orders = [line for line in differs.result_lines
                  if line.verdict is ComparisonVerdict.DIFFERENT and line.words == 'orders']
        assert orders, [(line.verdict, line.words) for line in differs.result_lines]
        assert any('lots' in field.folded_path for field in orders[0].differing_fields)

    def test_the_new_stand_s_second_run_agrees_with_its_first(self, differs):
        assert differs.second_run is not None and differs.second_run.failed_because == ''
        assert not any(record.differences or record.old_only_fields or record.new_only_fields
                       for record in differs.second_run_disagrees)
        assert not [line for line in differs.result_lines if line.unseen_because.startswith(
            'the new stand')]
