"""
FiniexTestingIDE - Config Name Validator

A configuration is addressed by its FILE NAME everywhere a record names it — a run's
`config_snapshot`, the run-config store's `source_name`, the scenario-set resolver, the config
directory's key. So one name may belong to one kind of configuration only: a scenario set and an
AutoTrader profile named alike would make every one of those records ambiguous, and the config
directory would show one file where there are two.

Refused, not warned about: a run started from such a name cannot be traced back afterwards, and a
warning read after the fact does not undo that. The check READS only — a walk of the
configuration roots, remembered per process, and a parse of the same-named files — so a live
start depends on nothing it has to write.
"""

from typing import Dict, List, Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.framework.config_directory.config_directory_builder import config_kind_of
from python.framework.config_directory.config_directory_discovery import (
    discover_all_config_files,
    location_label,
)
from python.framework.exceptions.config_name_errors import ConfigNameConflictError
from python.framework.types.config_directory_types import ConfigKind, DiscoveredConfigFile

# What each kind is called in the refusal.
_KIND_LABEL = {ConfigKind.SCENARIO_SET: 'a scenario set',
               ConfigKind.AUTOTRADER_PROFILE: 'an AutoTrader profile'}

# The walk, per set of roots: file name → every candidate of that name. Remembered for the life of
# the process — a run starts once, and a test suite loads configurations hundreds of times.
_CANDIDATES: Dict[Tuple[str, ...], Dict[str, List[DiscoveredConfigFile]]] = {}


def clear_config_name_memo() -> None:
    """Forget the remembered walk, so the next check sees the roots as they are now."""
    _CANDIDATES.clear()


def config_name_conflict(
    file_name: str, kind: ConfigKind, app_config: Optional[AppConfigManager] = None,
    candidates: Optional[List[DiscoveredConfigFile]] = None,
) -> Optional[str]:
    """
    Why a configuration's name is ambiguous, or None when it is not.

    Args:
        file_name: The configuration's file name
        kind: Its own kind
        app_config: Supplies the roots, when `candidates` is not given
        candidates: The same-named files to judge, when the caller already has them

    Returns:
        The reason — naming where each same-named file of the other kind lives, by origin and
        folder, never by a private path — or None
    """
    if candidates is None:
        candidates = _candidates_by_name(app_config or AppConfigManager()).get(file_name, [])
    others = [candidate for candidate in candidates
              if config_kind_of(candidate.path) not in (None, kind)]
    if not others:
        return None
    other_kind = config_kind_of(others[0].path)
    where = ', '.join(sorted({location_label(c.origin, c.folder) for c in others}))
    return (f"'{file_name}' is also the name of {_KIND_LABEL[other_kind]} ({where}). A run "
            f'records its configuration by file name, so the two could not be told apart — '
            f'rename one of them.')


def refuse_config_name_conflict(file_name: str, kind: ConfigKind) -> None:
    """
    Refuse to start a run whose configuration name belongs to the other kind as well.

    Args:
        file_name: The configuration's file name
        kind: Its own kind
    """
    reason = config_name_conflict(file_name, kind)
    if reason:
        raise ConfigNameConflictError(reason)


def _candidates_by_name(app_config: AppConfigManager) -> Dict[str, List[DiscoveredConfigFile]]:
    """
    Every configuration file under the roots, grouped by name — walked once per set of roots.

    Args:
        app_config: Supplies the roots

    Returns:
        file name → its candidates
    """
    key = (app_config.get_user_scenario_sets_path(), app_config.get_user_autotrader_profiles_path(),
           *app_config.get_user_algo_dirs(), app_config.get_scenario_sets_path(),
           app_config.get_autotrader_profiles_path())
    if key not in _CANDIDATES:
        grouped: Dict[str, List[DiscoveredConfigFile]] = {}
        for candidate in discover_all_config_files(app_config):
            grouped.setdefault(candidate.path.name, []).append(candidate)
        _CANDIDATES[key] = grouped
    return _CANDIDATES[key]
