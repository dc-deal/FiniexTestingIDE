"""
FiniexTestingIDE - Parameter Validator
Validates config values against InputParamDef schemas

Stateless utility functions for parameter validation and default injection.
Used by both Factories (Phase 6) and static validation (Phase 0).
"""

from typing import Any, Dict, List, Optional, Set

from python.framework.types.parameter_types import InputParamDef


def validate_parameters(
    config: Dict[str, Any],
    schema: Dict[str, InputParamDef],
    strict: bool = True,
    context_name: str = "",
    reserved_keys: Optional[Set[str]] = None,
) -> List[str]:
    """
    Validate config dict against parameter schema.

    Checks (in order):
    1. Required parameters are present
    2. Types match (always strict - type errors are never warnings)
    3. Values within min/max bounds (strict or warn)
    4. Values in choices (strict or warn)
    5. Unknown keys (opt-in via reserved_keys) — a config key that is neither a
       schema parameter nor a declared reserved/structural key is a typo, otherwise
       silently ignored at runtime

    Args:
        config: User-provided configuration dict
        schema: Parameter schema from get_parameter_schema()
        strict: True = raise on boundary violations, False = warn only
        context_name: Worker/Logic name for error messages
        reserved_keys: Non-schema keys the component legitimately accepts. When given
            (even empty), unknown keys are rejected; when None, the unknown-key check
            is skipped (backward-compatible default)

    Returns:
        List of warning messages (empty if all valid in strict mode)
    """
    if not schema and reserved_keys is None:
        return []

    schema = schema or {}
    warnings: List[str] = []
    prefix = f"'{context_name}': " if context_name else ""

    for param_name, param_def in schema.items():

        # --- Required check ---
        if param_name not in config:
            if param_def.is_required:
                raise ValueError(
                    f"{prefix}Required parameter '{param_name}' missing. "
                    f"{param_def.description}"
                )
            continue

        value = config[param_name]

        # --- Type check (always strict) ---
        if not _is_type_compatible(value, param_def.param_type):
            raise ValueError(
                f"{prefix}Parameter '{param_name}' has wrong type: "
                f"expected {param_def.param_type.__name__}, "
                f"got {type(value).__name__} (value={value})"
            )

        # --- Min bound check ---
        if param_def.min_val is not None and value < param_def.min_val:
            msg = (
                f"{prefix}Parameter '{param_name}' value {value} "
                f"is below minimum {param_def.min_val}"
            )
            if strict:
                raise ValueError(msg)
            warnings.append(msg)

        # --- Max bound check ---
        if param_def.max_val is not None and value > param_def.max_val:
            msg = (
                f"{prefix}Parameter '{param_name}' value {value} "
                f"is above maximum {param_def.max_val}"
            )
            if strict:
                raise ValueError(msg)
            warnings.append(msg)

        # --- Choices check ---
        if param_def.choices is not None and value not in param_def.choices:
            msg = (
                f"{prefix}Parameter '{param_name}' value '{value}' "
                f"not in allowed choices: {param_def.choices}"
            )
            if strict:
                raise ValueError(msg)
            warnings.append(msg)

    # --- Unknown-key check (opt-in) ---
    # Keys starting with '_' are the config-file comment/metadata convention
    # (JSON has no comments, e.g. '_comment') — never strategy parameters, so they
    # are allowed without weakening typo detection on real parameters.
    if reserved_keys is not None:
        allowed = set(schema) | reserved_keys
        for key in config:
            if key.startswith('_') or key in allowed:
                continue
            msg = (
                f"{prefix}Unknown parameter '{key}' — not in the component "
                f"schema. Known parameters: {sorted(allowed)}"
            )
            if strict:
                raise ValueError(msg)
            warnings.append(msg)

    return warnings


def apply_defaults(
    config: Dict[str, Any],
    schema: Dict[str, InputParamDef],
) -> Dict[str, Any]:
    """
    Merge config with defaults from parameter schema.

    Schema defaults fill in missing config values.
    Existing config values are preserved (user overrides defaults).

    Args:
        config: User-provided configuration dict
        schema: Parameter schema with defaults

    Returns:
        Merged config dict (copy, original unchanged)
    """
    merged = dict(config)

    for param_name, param_def in schema.items():
        if param_name not in merged and not param_def.is_required:
            merged[param_name] = param_def.default

    return merged


def _is_type_compatible(value: Any, expected_type: type) -> bool:
    """
    Check if value matches expected type with numeric coercion.

    Allows int where float is expected (int is a valid float).
    All other types require exact match.

    Args:
        value: Value to check
        expected_type: Expected Python type

    Returns:
        True if type is compatible
    """
    if isinstance(value, expected_type):
        return True

    # int is acceptable where float is expected
    if expected_type == float and isinstance(value, int):
        return True

    return False
