"""
FiniexTestingIDE - Config Directory Discovery (#554)

The walk that finds every file that could be a run configuration — and nothing else: a path, a
`stat`, and the root it was found under. No file is opened here, nothing is created and nothing is
logged; the reading happens in the builder, and only for files whose `stat` changed.

The roots and their precedence are the scenario-set resolver's (`scenario_set_resolver.py`):
`user_configs/` over the user algo directories over `configs/`. A file name is the identity a run
records (`config_snapshot`), so a same-named file lower in that order is SHADOWED — listed on the
winner, never served as a second row.
"""

import os
from pathlib import Path
from typing import Dict, List, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.config_directory_types import ConfigOrigin, DiscoveredConfigFile

# Directories a walk never enters by name. Hidden ones are skipped by their leading dot — the
# run-patch home a strategy repository keeps inside itself (#551) among them.
_SKIPPED_DIR_NAMES = frozenset({'__pycache__', 'node_modules'})


def discover_config_files(app_config: AppConfigManager) -> List[DiscoveredConfigFile]:
    """
    Every candidate configuration file, one per file name, the precedence winner.

    Args:
        app_config: Supplies the roots

    Returns:
        The winners, each carrying the origins it shadows; sorted by file name
    """
    found: Dict[str, List[DiscoveredConfigFile]] = {}
    for root, origin, keeps_folder in _roots(app_config):
        for candidate in _walk(root, origin, keeps_folder):
            found.setdefault(candidate.path.name, []).append(candidate)

    winners = []
    rank = {origin: position for position, origin in enumerate(ConfigOrigin)}
    for name in sorted(found):
        ranked = sorted(found[name], key=lambda candidate: rank[candidate.origin])
        winner = ranked[0]
        winner.shadowed = [candidate.origin for candidate in ranked[1:]]
        winners.append(winner)
    return winners


def _roots(app_config: AppConfigManager) -> List[Tuple[Path, ConfigOrigin, bool]]:
    """
    The directories the walk covers, each with its origin.

    Args:
        app_config: Supplies the configured paths

    Returns:
        (root, origin, whether its sub-folder is served) per root
    """
    roots = [
        (Path(app_config.get_user_scenario_sets_path()), ConfigOrigin.USER_CONFIGS, False),
        (Path(app_config.get_user_autotrader_profiles_path()), ConfigOrigin.USER_CONFIGS, False),
    ]
    roots += [(Path(directory), ConfigOrigin.USER_ALGOS, False)
              for directory in app_config.get_user_algo_dirs()]
    roots += [
        (Path(app_config.get_scenario_sets_path()), ConfigOrigin.CONFIGS, True),
        (Path(app_config.get_autotrader_profiles_path()), ConfigOrigin.CONFIGS, True),
    ]
    return roots


def _walk(root: Path, origin: ConfigOrigin, keeps_folder: bool) -> List[DiscoveredConfigFile]:
    """
    The JSON files under one root, recursively, skipping hidden and bytecode directories.

    Args:
        root: The directory to walk; a missing one yields nothing and is never created
        origin: Its origin
        keeps_folder: Whether the sub-folder of each file is recorded

    Returns:
        One candidate per JSON file
    """
    if not root.is_dir():
        return []
    candidates = []
    # Pruned BEFORE descending, not filtered after: measured 2026-09-27, 241 of the 275 entries
    # under `user_algos/` sit in its `.git`, and every one is a request across the bridged mount
    # (§42) — a filter after `rglob` paid ~640 ms to walk what it then threw away. Sorted, so a
    # same-named file inside one root resolves the same way on every walk.
    for directory, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(name for name in dir_names
                              if not name.startswith('.') and name not in _SKIPPED_DIR_NAMES)
        for file_name in sorted(file_names):
            if file_name.startswith('.') or not file_name.endswith('.json'):
                continue
            path = Path(directory) / file_name
            try:
                stat = path.stat()
            except OSError:
                continue
            relative = path.relative_to(root)
            folder = relative.parent.as_posix() if keeps_folder and relative.parent.parts else ''
            candidates.append(DiscoveredConfigFile(
                path=path, origin=origin, folder=folder, mtime=stat.st_mtime, size=stat.st_size))
    return candidates
