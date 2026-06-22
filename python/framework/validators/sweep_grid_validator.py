"""
Sweep-grid validator (#390).

STRUCTURAL fail-fast validation of a sweep grid BEFORE any batch runs: every dotted path must
resolve to a real parameter of the right component (decision logic / worker). A spec-structure
error (unknown path / param / worker, empty value list) affects all combinations and is a clear
typo → abort the whole sweep early.

Value-RANGE / type violations are deliberately NOT checked here: they are a per-combination
concern (§33 = config error → per-scenario failure, not a whole-batch abort). An out-of-range
value flows into its combination's run, fails it at setup, and is recorded as an error-flagged
ledger row that is excluded from the ranking — the other combinations keep running.
"""

from typing import Any, Dict, List

from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.abstract_logger import AbstractLogger


def validate_sweep_grid(
    grid: Dict[str, List[Any]],
    strategy_config: Dict[str, Any],
    logger: AbstractLogger,
) -> None:
    """
    Validate the grid's structure against the target components' parameter schemas.

    Args:
        grid: Sweep grid (dotted path → candidate values)
        strategy_config: The base scenario set's strategy_config (decision + worker types)
        logger: Logger for the factories

    Raises:
        ValueError: On an empty value list, an unknown path, param, or worker instance
    """
    decision_type = strategy_config.get('decision_logic_type', '')
    worker_instances = strategy_config.get('worker_instances', {})
    decision_factory = DecisionLogicFactory(logger)
    worker_factory = WorkerFactory(logger)

    for path, values in grid.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"Grid path '{path}' must map to a non-empty list of values")
        _check_param_exists(
            path, decision_type, worker_instances, decision_factory, worker_factory)


def _check_param_exists(
    path: str,
    decision_type: str,
    worker_instances: Dict[str, str],
    decision_factory: DecisionLogicFactory,
    worker_factory: WorkerFactory,
) -> None:
    """Raise if a dotted grid path does not resolve to a real parameter of its component."""
    parts = path.split('.')

    if parts[0] == 'decision_logic_config':
        if len(parts) != 2:
            raise ValueError(f"Grid path '{path}' must be 'decision_logic_config.<param>'")
        param = parts[1]
        component_class, _ = decision_factory.resolve_logic_class(decision_type)
    elif parts[0] == 'workers':
        if len(parts) != 3:
            raise ValueError(f"Grid path '{path}' must be 'workers.<instance>.<param>'")
        worker_name, param = parts[1], parts[2]
        worker_type = worker_instances.get(worker_name)
        if worker_type is None:
            raise ValueError(
                f"Grid path '{path}': unknown worker instance '{worker_name}'. "
                f"Known: {sorted(worker_instances)}")
        component_class, _ = worker_factory.resolve_worker_class(worker_type)
    else:
        raise ValueError(
            f"Grid path '{path}' must start with 'decision_logic_config.' or 'workers.<instance>.'")

    schema = component_class.get_parameter_schema()
    if param not in schema:
        raise ValueError(
            f"Grid path '{path}': parameter '{param}' not in {component_class.__name__} schema. "
            f"Known: {sorted(schema)}")
