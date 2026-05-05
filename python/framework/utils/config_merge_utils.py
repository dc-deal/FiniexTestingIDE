"""
FiniexTestingIDE - Config Merge Utilities
Shared deep merge helper for all configuration loaders.
"""

import copy
from typing import Any, Dict, Optional, Set


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
