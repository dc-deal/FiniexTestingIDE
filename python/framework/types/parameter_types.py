"""
FiniexTestingIDE - Parameter Type Definitions
Type-safe parameter schema for Workers and Decision Logics

Provides ParameterDef for declaring configurable parameters with
type, default, range, and description metadata.

Future: UX layer can consume these definitions for sliders, dropdowns, etc.

Location: python/framework/types/parameter_types.py
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


class _RequiredSentinel:
    """Sentinel class indicating a parameter must be provided in config."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "REQUIRED"

    def __bool__(self) -> bool:
        return False


# Public sentinel - use as ParameterDef(default=REQUIRED)
REQUIRED = _RequiredSentinel()


@dataclass(frozen=True)
class ParameterDef:
    """
    Definition of a configurable parameter.

    Used by Workers and Decision Logics to declare parameters
    with type safety, validation ranges, and defaults.

    Args:
        param_type: Python type (float, int, bool, str)
        default: Default value. Use REQUIRED sentinel when parameter must be provided.
        min_val: Minimum value inclusive (numeric types only)
        max_val: Maximum value inclusive (numeric types only)
        choices: Allowed values for enum/dropdown parameters (tuple for immutability)
        description: Functional description (not UX - like a docstring)

    Returns:
        Immutable parameter definition
    """
    param_type: type
    default: Any = REQUIRED
    min_val: Any = None
    max_val: Any = None
    choices: Optional[tuple] = None
    description: str = ""

    @property
    def is_required(self) -> bool:
        """True if parameter must be provided (no default)."""
        return isinstance(self.default, _RequiredSentinel)


class ValidatedParameters:
    """
    Immutable parameter access after validation + default application.

    Created by Factory AFTER validate_parameter_schema() + apply_defaults().
    Guarantees: every schema-declared parameter has a value.

    Usage in Workers / Decision Logics:
        self.deviation = self.params.get('deviation')   # No default!
        self.periods   = self.params.get('periods')     # Guaranteed present

    The deliberate absence of a default parameter in .get() is the
    safety mechanism. If a key is missing, it means:
      - Not declared in get_parameter_schema() → add it there
      - Factory didn't run apply_defaults() → bug in factory pipeline
      - Typo in key name → fix the string

    Note: Also accepts raw dict construction for test convenience:
        worker = RSIWorker(parameters={"periods": {"M5": 4}}, ...)
    The AbstractWorker/AbstractDecisionLogic will auto-wrap dicts.
    """

    def __init__(self, data: dict):
        """
        Wrap a validated config dict.

        Args:
            data: Config dict (already validated + defaults applied by Factory)
        """
        self._data = dict(data)  # defensive copy

    def get(self, key: str):
        """
        Get validated parameter value. NO default parameter by design.

        This forces callers to rely on the schema's default (applied by
        Factory) instead of re-declaring defaults in every Worker/Logic.

        Args:
            key: Parameter name (must match get_parameter_schema() key
                 or infrastructure key like 'periods')

        Returns:
            Parameter value

        Raises:
            KeyError: With actionable message if key not found
        """
        if key not in self._data:
            raise KeyError(
                f"Parameter '{key}' not found in ValidatedParameters. "
                f"Available keys: {list(self._data.keys())}. "
                f"Declare it in get_parameter_schema() or check spelling."
            )
        return self._data[key]

    def has(self, key: str) -> bool:
        """Check if parameter exists (for optional/infrastructure keys)."""
        return key in self._data

    def as_dict(self) -> dict:
        """
        Return raw dict copy for external consumers.

        Used by WorkerOrchestrator._extract_worker_type() and similar
        code that reads .parameters as a plain dict.
        """
        return dict(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"ValidatedParameters({list(self._data.keys())})"
