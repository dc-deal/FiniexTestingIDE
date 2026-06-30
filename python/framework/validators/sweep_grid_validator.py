"""
Sweep-grid validator (#390).

STRUCTURAL fail-fast validation of a sweep grid BEFORE any batch runs: every dotted path
must have a valid shape (`decision_logic_config.<param>[.<sub>…]` or
`workers.<instance>.<param>[.<sub>…]` — nested paths allowed, e.g. `workers.macd_main.periods.M5`)
and a non-empty list of values. A spec-structure error affects all combinations and is a
clear typo → abort the whole sweep early.

Parameter EXISTENCE and value RANGE are deliberately NOT checked here. Both are now a per-run
concern: the grid only writes values into the scenario's strategy_config, and the run's
Phase 0 (ScenarioValidator.validate_scenario_parameters) validates each combination's params
against the component schemas (type / range / required / unknown). An invalid combination is
marked invalid there, excluded from execution, and recorded as an error-flagged ledger row
that is left out of the ranking (#1) — the other combinations keep running (§33).
"""

from typing import Any, Dict, List

from python.framework.logging.abstract_logger import AbstractLogger


def validate_sweep_grid(
    grid: Dict[str, List[Any]],
    logger: AbstractLogger,
) -> None:
    """
    Validate the grid's structure (path shape + non-empty value lists).

    Args:
        grid: Sweep grid (dotted path → candidate values)
        logger: Logger (unused here; kept for a uniform validator signature)

    Raises:
        ValueError: On an empty value list or a malformed dotted path
    """
    for path, values in grid.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"Grid path '{path}' must map to a non-empty list of values")
        _check_path_shape(path)


def _check_path_shape(path: str) -> None:
    """Raise if a dotted grid path is not a valid decision/worker parameter path shape."""
    parts = path.split('.')

    if parts[0] == 'decision_logic_config':
        if len(parts) < 2:
            raise ValueError(
                f"Grid path '{path}' must be 'decision_logic_config.<param>[.<sub>…]'")
    elif parts[0] == 'workers':
        if len(parts) < 3:
            raise ValueError(
                f"Grid path '{path}' must be 'workers.<instance>.<param>[.<sub>…]'")
    else:
        raise ValueError(
            f"Grid path '{path}' must start with 'decision_logic_config.' or 'workers.<instance>.'")
