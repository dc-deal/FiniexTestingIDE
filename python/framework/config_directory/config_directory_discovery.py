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
        The winners, each carrying the origins it shadows and those files; sorted by file name
    """
    found: Dict[str, List[DiscoveredConfigFile]] = {}
    for candidate in discover_all_config_files(app_config):
        found.setdefault(candidate.path.name, []).append(candidate)

    winners = []
    rank = {origin: position for position, origin in enumerate(ConfigOrigin)}
    for name in sorted(found):
        ranked = sorted(found[name], key=lambda candidate: rank[candidate.origin])
        winner = ranked[0]
        winner.shadowed = [candidate.origin for candidate in ranked[1:]]
        winner.shadowed_files = ranked[1:]
        winners.append(winner)
    return winners


def discover_all_config_files(app_config: AppConfigManager) -> List[DiscoveredConfigFile]:
    """
    Every candidate configuration file under every root — same-named ones included.

    Args:
        app_config: Supplies the roots

    Returns:
        One candidate per JSON file, in root order
    """
    candidates: List[DiscoveredConfigFile] = []
    for root, origin, keeps_folder in _roots(app_config):
        candidates.extend(_walk(root, origin, keeps_folder))
    return candidates


def is_user_owned(path: Path) -> bool:
    """
    Whether a configuration file is the user's own — under `user_configs/` or a user algo
    directory, the roots the walk covers besides `configs/` (#576).

    A file anywhere else is neither: a test's temporary copy and a fixture production's workspace
    belong to no root, and keep what they declare.

    Args:
        path: The configuration file

    Returns:
        True when it lies under one of those roots
    """
    resolved = Path(path).resolve()
    return any(resolved.is_relative_to(root.resolve())
               for root, origin, _ in _roots(AppConfigManager())
               if origin is not ConfigOrigin.CONFIGS)


def profile_homes(app_config: AppConfigManager) -> List[Path]:
    """
    Every root an AutoTrader profile may live under — the shipped and the workspace profile trees
    and each user algo directory (#581).

    Args:
        app_config: Supplies the configured paths

    Returns:
        The roots, as configured
    """
    return [Path(app_config.get_autotrader_profiles_path()),
            Path(app_config.get_user_autotrader_profiles_path()),
            *(Path(directory) for directory in app_config.get_user_algo_dirs())]


def location_label(origin: ConfigOrigin, folder: str) -> str:
    """
    Where a configuration file lives, as the directory names it — never an operator's own path.

    Args:
        origin: The root it lives under
        folder: Its sub-folder inside `configs/`, '' otherwise

    Returns:
        `configs/backtesting`, `user_algos`, …
    """
    return f'{origin.value}/{folder}' if folder else origin.value


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


def json_files_under(root: Path) -> List[Path]:
    """
    The JSON files under one directory, recursively, skipping hidden and bytecode directories —
    the walk every reader of the configuration roots shares, so none of them crosses a `.git`.

    Args:
        root: The directory to walk; a missing one yields nothing and is never created

    Returns:
        The files, sorted within each directory
    """
    if not root.is_dir():
        return []
    files: List[Path] = []
    # Pruned BEFORE descending, not filtered after: measured 2026-09-27, 241 of the 275 entries
    # under `user_algos/` sit in its `.git`, and every one is a request across the bridged mount
    # (§42) — a filter after `rglob` paid ~640 ms to walk what it then threw away. Sorted, so a
    # same-named file inside one root resolves the same way on every walk.
    for directory, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(name for name in dir_names
                              if not name.startswith('.') and name not in _SKIPPED_DIR_NAMES)
        files += [Path(directory) / file_name for file_name in sorted(file_names)
                  if not file_name.startswith('.') and file_name.endswith('.json')]
    return files


def _walk(root: Path, origin: ConfigOrigin, keeps_folder: bool) -> List[DiscoveredConfigFile]:
    """
    The JSON files under one root as candidates, each with its stat.

    Args:
        root: The directory to walk; a missing one yields nothing and is never created
        origin: Its origin
        keeps_folder: Whether the sub-folder of each file is recorded

    Returns:
        One candidate per JSON file
    """
    candidates = []
    for path in json_files_under(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        relative = path.relative_to(root)
        folder = relative.parent.as_posix() if keeps_folder and relative.parent.parts else ''
        candidates.append(DiscoveredConfigFile(
            path=path, origin=origin, folder=folder, mtime=stat.st_mtime, size=stat.st_size))
    return candidates
