"""
FiniexTestingIDE - Config Merge Utilities
Shared deep merge helper for all configuration loaders.
"""

import copy
import os
from typing import Any, Dict, Optional, Set, Type

from pydantic import BaseModel, ValidationError


_CONFIG_ISOLATION_ENV: str = 'FINIEX_CONFIG_ISOLATION'


def is_config_isolation_active() -> bool:
    """
    Returns True when user_configs/ overrides must be skipped.

    Tests must be deterministic across developers — the test runner's
    conftest.py sets FINIEX_CONFIG_ISOLATION=1 at session start so no
    loader pulls in personal workspace overrides during pytest.

    Production runs leave the env var unset and the normal cascade
    (base config → user_configs/* override) applies as designed.

    Returns:
        True if the env var FINIEX_CONFIG_ISOLATION is set to a truthy value
    """
    return os.environ.get(_CONFIG_ISOLATION_ENV, '').lower() in ('1', 'true', 'yes')


def deep_merge(
    base: Dict[str, Any],
    override: Dict[str, Any],
    atomic_keys: Optional[Set[str]] = None,
    list_merge_keys: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Recursively merge override dict into base dict.

    Nested dicts are merged deeply. Atomic keys are replaced entirely. Lists
    whose containing key appears in list_merge_keys are merged element-wise via
    an identifier field (matching entries deep-merged, base-only kept,
    override-only appended). All other values (primitives, plain lists) are
    replaced by override.

    Args:
        base: Base configuration dict
        override: Override dict — takes precedence over base
        atomic_keys: Keys replaced entirely, never deep-merged (e.g. {'balances'})
        list_merge_keys: Maps list-field name → identifier key for element-wise
            merge (e.g. {'brokers': 'broker_type'}). Each override entry in such
            a list must declare its identifier field.

    Returns:
        New merged dict — inputs are not mutated
    """
    _atomic = atomic_keys or set()
    _list_keys = list_merge_keys or {}
    result = copy.deepcopy(base)

    for key, override_value in override.items():
        if key in _atomic:
            result[key] = copy.deepcopy(override_value)
        elif (
            key in _list_keys
            and key in result
            and isinstance(result[key], list)
            and isinstance(override_value, list)
        ):
            result[key] = _merge_lists_by_key(
                result[key], override_value, _list_keys[key], _atomic, _list_keys
            )
        elif key in result and isinstance(result[key], dict) and isinstance(override_value, dict):
            result[key] = deep_merge(result[key], override_value, _atomic, _list_keys)
        else:
            result[key] = copy.deepcopy(override_value)

    return result


def _merge_lists_by_key(
    base_list: list,
    override_list: list,
    id_key: str,
    atomic_keys: Set[str],
    list_merge_keys: Dict[str, str],
) -> list:
    """
    Merge two lists of dicts element-wise by an identifier field.

    Entries are matched by id_key value. Matching entries are deep-merged,
    base-only entries are preserved in their original order, override-only
    entries are appended at the end.

    Args:
        base_list: List from base config
        override_list: List from override
        id_key: Field name used to match entries (e.g. 'broker_type')
        atomic_keys: Forwarded to nested deep_merge calls
        list_merge_keys: Forwarded to nested deep_merge calls

    Returns:
        New merged list of dicts
    """
    by_id: Dict[Any, Dict[str, Any]] = {entry[id_key]: copy.deepcopy(entry) for entry in base_list}

    for override_entry in override_list:
        if id_key not in override_entry:
            raise ValueError(
                f"List-merge override entry missing required '{id_key}' identifier — "
                f"each entry in a list-merge field must declare its identifier"
            )
        entry_id = override_entry[id_key]
        if entry_id in by_id:
            by_id[entry_id] = deep_merge(
                by_id[entry_id], override_entry, atomic_keys, list_merge_keys
            )
        else:
            by_id[entry_id] = copy.deepcopy(override_entry)

    return list(by_id.values())


# Keys allowed in any config section — JSON documentation convention.
_CONFIG_META_KEYS: frozenset = frozenset({'_comment'})


def check_unknown_keys(
    location: str,
    config: Dict[str, Any],
    known: frozenset,
) -> None:
    """
    Raise if config contains keys absent from the known set.

    Used by config loaders before deep_merge to detect typos with full
    level provenance (global vs. per-scenario, section name).
    Hard fail — unknown keys indicate a structural misconfiguration.
    Meta keys (e.g. '_comment') are universally allowed.

    Args:
        location: Human-readable path (e.g. 'global.execution_config')
        config: Raw config dict to inspect
        known: Set of valid key names for this section
    """
    unknown = set(config.keys()) - known - _CONFIG_META_KEYS
    if unknown:
        raise ValueError(f"Unknown keys in {location}: {unknown} — check for typos or add to known keys")


def validate_merged_config(
    model_class: Type[BaseModel],
    config: Dict[str, Any],
    location: str,
) -> None:
    """
    Validate a merged config dict against a Pydantic model.

    Used by config loaders after deep_merge to catch type errors with full
    level provenance. Hard fail — wrong value types indicate misconfiguration.

    Args:
        model_class: Pydantic model class to validate against
        config: Merged config dict
        location: Human-readable path for error messages (e.g. 'execution_config[my_scenario]')
    """
    if not config:
        return
    try:
        model_class.model_validate(config)
    except ValidationError as e:
        raise ValueError(f"Type error in {location}: {e}") from e
