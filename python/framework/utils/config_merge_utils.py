"""
FiniexTestingIDE - Config Merge Utilities
Shared deep merge helper for all configuration loaders.
"""

import copy
from typing import Any, Dict, Optional, Set, Type

from pydantic import BaseModel, ValidationError


def deep_merge(
    base: Dict[str, Any],
    override: Dict[str, Any],
    atomic_keys: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """
    Recursively merge override dict into base dict.

    Nested dicts are merged deeply. Atomic keys are replaced entirely, never
    deep-merged. All other values (primitives, lists) are replaced by override.

    Args:
        base: Base configuration dict
        override: Override dict — takes precedence over base
        atomic_keys: Keys replaced entirely, never deep-merged (e.g. {'balances'})

    Returns:
        New merged dict — inputs are not mutated
    """
    _atomic = atomic_keys or set()
    result = copy.deepcopy(base)

    for key, override_value in override.items():
        if key in _atomic:
            result[key] = copy.deepcopy(override_value)
        elif key in result and isinstance(result[key], dict) and isinstance(override_value, dict):
            result[key] = deep_merge(result[key], override_value, _atomic)
        else:
            result[key] = copy.deepcopy(override_value)

    return result


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
