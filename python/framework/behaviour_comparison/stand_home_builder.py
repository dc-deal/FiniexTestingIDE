"""
Builds one stand of a behaviour comparison (#641): its code tree at the framework commit, and a
scratch data home beside it that holds the strategy repository at the strategy commit.

The home is laid out like the one this process runs on, so the scenario set's relative strategy
paths name the same files in it. What a run may rewrite — the indexes, the caches, the broker
cache — is copied; the archive and the workspace are linked, because a backtest only reads them.
Everything stays below the stand's folder, which the comparison removes when it ends.
"""

import os
import shutil
from pathlib import Path
from typing import Optional

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.kraken_config_fetcher import RUNTIME_CACHE_BASE
from python.configuration.data_home import (
    WORKSPACE_DIR,
    relative_in_data_home,
    resolve_in_data_home,
    workspace_path,
)
from python.framework.constants.behaviour_comparison_constants import STAND_HOME_COPIED_PROCESSED
from python.framework.types.behaviour_comparison_types import ComparisonStandRun
from python.framework.utils.file_utils import create_directory
from python.framework.utils.git_info_utils import clone_at_commit


class StandHomeBuilder:
    """
    Builds a stand's tree and scratch data home.

    Args:
        framework_root: The framework repository the trees are cloned from
        strategy_root: The strategy repository; None where no strategy repository takes part
    """

    def __init__(self, framework_root: str, strategy_root: Optional[str] = None):
        self._framework_root = framework_root
        self._strategy_root = strategy_root

    def build(self, run: ComparisonStandRun, stand_dir: Path) -> ComparisonStandRun:
        """
        One stand, built below its folder.

        Args:
            run: The stand, not built yet
            stand_dir: Its folder, inside the comparison's scratch folder

        Returns:
            The stand with its tree and home; with failed_because where it could not be built
        """
        tree = stand_dir / 'tree'
        home = create_directory(stand_dir / 'home',
                                'a behaviour comparison stand\'s scratch data home')
        built = ComparisonStandRun(stand=run.stand, commits=run.commits, tree=str(tree),
                                   home=str(home))
        cloned = clone_at_commit(self._framework_root, run.commits.framework_commit, tree)
        if cloned:
            return _failed(built, f'its tree could not be cloned at framework commit '
                                  f'{run.commits.framework_commit[:12]}: {cloned}')
        if run.commits.strategy_commit is not None:
            failure = self._clone_strategy(home, run.commits.strategy_commit)
            if failure:
                return _failed(built, failure)
        self._link_workspace(home)
        failure = self._lay_out_processed(home)
        if failure:
            return _failed(built, failure)
        self._copy_broker_cache(home)
        return built

    def _clone_strategy(self, home: Path, commit: str) -> str:
        """
        The strategy repository at its commit, where the home's strategy paths expect it.

        Args:
            home: The stand's home
            commit: The strategy commit

        Returns:
            Why it could not be placed; '' where it was
        """
        relative = relative_in_data_home(self._strategy_root or '')
        if relative is None:
            return (f'the strategy repository {self._strategy_root} lies outside the data home, so '
                    f'the scenario set\'s strategy paths cannot name it in a second home')
        cloned = clone_at_commit(self._strategy_root or '', commit, home / relative)
        if cloned:
            return (f'the strategy repository could not be cloned at commit {commit[:12]}: '
                    f'{cloned}')
        return ''

    @staticmethod
    def _link_workspace(home: Path) -> None:
        """
        The operator's workspace, linked into the home — a backtest reads its overrides and
        writes nothing there; a workspace that does not exist is left out.

        Args:
            home: The stand's home
        """
        workspace = Path(workspace_path()).absolute()
        if workspace.exists():
            os.symlink(workspace, home / WORKSPACE_DIR, target_is_directory=True)

    @staticmethod
    def _lay_out_processed(home: Path) -> str:
        """
        The processed-data directory: the indexes and caches copied, the archive linked.

        Args:
            home: The stand's home

        Returns:
            Why it could not be laid out; '' where it was
        """
        source = Path(AppConfigManager().get_data_processed_path()).absolute()
        relative = relative_in_data_home(AppConfigManager().get_data_processed_path())
        if relative is None:
            return (f'the processed data {source} lies outside the data home, so a second home '
                    f'cannot be laid out like this one')
        target = create_directory(home / relative, 'a behaviour comparison stand\'s processed data')
        if not source.is_dir():
            return f'there is no processed data at {source}'
        for entry in sorted(source.iterdir()):
            destination = target / entry.name
            if entry.name in STAND_HOME_COPIED_PROCESSED:
                if entry.is_dir():
                    shutil.copytree(entry, destination, symlinks=True)
                else:
                    shutil.copy2(entry, destination)
            elif entry.is_dir():
                os.symlink(entry.resolve(), destination, target_is_directory=True)
            else:
                shutil.copy2(entry, destination)
        return ''

    @staticmethod
    def _copy_broker_cache(home: Path) -> None:
        """
        The runtime broker cache, copied — a dynamic broker's symbol specifications come from it,
        and a backtest may refresh it.

        Args:
            home: The stand's home
        """
        source = Path(resolve_in_data_home(RUNTIME_CACHE_BASE)).absolute()
        if source.is_dir():
            create_directory((home / RUNTIME_CACHE_BASE).parent,
                             'a behaviour comparison stand\'s runtime data')
            shutil.copytree(source, home / RUNTIME_CACHE_BASE, symlinks=True)


def _failed(run: ComparisonStandRun, because: str) -> ComparisonStandRun:
    """
    A stand that could not be built.

    Args:
        run: The stand
        because: Why

    Returns:
        The stand with its reason
    """
    run.failed_because = because
    return run
