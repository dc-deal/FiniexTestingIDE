"""
A stand's tree and scratch data home (#641): the tree at its framework commit, the strategy
repository at its commit where the home's strategy paths expect it, the caches copied, the archive
and the workspace linked — built from a data home laid out in the test.
"""

from pathlib import Path

import pytest

from python.configuration.autotrader.kraken_config_fetcher import RUNTIME_CACHE_BASE
from python.framework.behaviour_comparison.stand_home_builder import StandHomeBuilder
from python.framework.types.behaviour_comparison_types import (
    ComparisonStand,
    ComparisonStandRun,
    StandCommits,
)
from python.framework.utils.git_info_utils import clear_git_caches
from tests.shared.git_test_repos import commit_all, make_repo, run_git, write_files


@pytest.fixture
def source_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / 'source_home'
    write_files(home, {
        'data/processed/bars_index.parquet': b'bars-index',
        'data/processed/ticks_index.parquet': b'ticks-index',
        'data/processed/discovery_caches/coverage.json': '{}',
        'data/processed/signals/signals_index.parquet': b'signals',
        'data/processed/kraken_spot/ETHUSD/ticks.parquet': b'archive',
        f'{RUNTIME_CACHE_BASE}/kraken_spot/kraken_spot_broker_config.json': '{"symbols": {}}',
        'user_configs/app_config.json': '{}',
    })
    make_repo(home / 'user_algos', {'my_algo/my_strategy.py': 'VERSION = 1\n'})
    monkeypatch.setenv('FINIEX_DATA_HOME', str(home))
    clear_git_caches()
    yield home
    clear_git_caches()


@pytest.fixture
def framework(tmp_path: Path) -> Path:
    return make_repo(tmp_path / 'framework', {'python/x.py': 'x = 1\n'})


def _stand(framework: Path, strategy: Path) -> ComparisonStandRun:
    return ComparisonStandRun(stand=ComparisonStand.OLD, commits=StandCommits(
        framework_commit=run_git(framework, 'rev-parse', 'HEAD'),
        strategy_commit=run_git(strategy, 'rev-parse', 'HEAD')))


class TestTheStandHome:

    def test_copies_are_copies_and_the_archive_and_workspace_are_links(self, source_home,
                                                                       framework, tmp_path):
        strategy = source_home / 'user_algos'
        built = StandHomeBuilder(str(framework), str(strategy)).build(
            _stand(framework, strategy), tmp_path / 'stand')
        assert built.failed_because == ''
        home = Path(built.home)
        processed = home / 'data' / 'processed'
        for name in ('bars_index.parquet', 'ticks_index.parquet', 'discovery_caches', 'signals'):
            assert (processed / name).exists() and not (processed / name).is_symlink()
        assert (processed / 'bars_index.parquet').read_bytes() == b'bars-index'
        assert (processed / 'kraken_spot').is_symlink()
        assert (processed / 'kraken_spot').resolve() == (
            source_home / 'data' / 'processed' / 'kraken_spot').resolve()
        assert (home / 'user_configs').is_symlink()
        cache = home / RUNTIME_CACHE_BASE / 'kraken_spot' / 'kraken_spot_broker_config.json'
        assert cache.exists() and not cache.is_symlink()

    def test_the_tree_and_the_strategy_stand_at_their_commits(self, source_home, framework,
                                                              tmp_path):
        strategy = source_home / 'user_algos'
        old = _stand(framework, strategy)
        write_files(strategy, {'my_algo/my_strategy.py': 'VERSION = 2\n'})
        commit_all(strategy, 'version 2')
        built = StandHomeBuilder(str(framework), str(strategy)).build(old, tmp_path / 'stand')
        assert (Path(built.tree) / 'python' / 'x.py').exists()
        clone = Path(built.home) / 'user_algos' / 'my_algo' / 'my_strategy.py'
        assert clone.read_text(encoding='utf-8') == 'VERSION = 1\n'

    def test_a_strategy_repository_outside_the_home_is_refused(self, source_home, framework,
                                                               tmp_path):
        outside = make_repo(tmp_path / 'elsewhere', {'s.py': 'x = 1\n'})
        built = StandHomeBuilder(str(framework), str(outside)).build(
            _stand(framework, outside), tmp_path / 'stand')
        assert 'outside the data home' in built.failed_because

    def test_an_unknown_framework_commit_is_refused(self, source_home, framework, tmp_path):
        run = ComparisonStandRun(stand=ComparisonStand.NEW,
                                 commits=StandCommits(framework_commit='0' * 40))
        built = StandHomeBuilder(str(framework)).build(run, tmp_path / 'stand')
        assert 'could not be cloned' in built.failed_because
