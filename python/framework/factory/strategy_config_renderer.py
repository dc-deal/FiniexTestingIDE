"""
Strategy config renderer (#547) — a strategy as its components are built, one rule for both
pipelines.

An AutoTrader session renders its profile's strategy before its header is written, and a backtest
renders each scenario's strategy at the same moment of its own start. Both go through the
factories' `resolve_parameters`, the helper their `create_*` uses, so a record of what a run ran
with is what its components were built with, every schema default included.

The type key a factory injects names the file it RESOLVED: for a component configured as a
relative file path that is an absolute path, which depends on the working directory and on the
code tree the run started from. A record names each component the way its configuration does
instead, so one strategy reads the same on every machine and in every release tree — comparing a
live session with its backtest depends on that. A check of a built component projects its key the
same way.
"""

from typing import Any, Dict

from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.utils.config_fingerprint_utils import to_plain

# The key each factory injects its resolved component type under.
LOGIC_TYPE_KEY = 'decision_logic_type'
WORKER_TYPE_KEY = 'worker_type'

# The two strategy keys that are replaced by their rendered form rather than copied.
_RENDERED_STRATEGY_KEYS = frozenset({'decision_logic_config', 'workers'})


def render_strategy_config(strategy_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Render a strategy as its components are built: every schema default filled in, and each
    component named the way the configuration names it.

    Only the worker instances the strategy actually runs are rendered — a `workers` entry that no
    instance names configures nothing.

    Args:
        strategy_config: The strategy as the run was given it, after the cascade

    Returns:
        The rendered strategy
    """
    logger = get_global_logger()
    rendered = {
        key: to_plain(value)
        for key, value in strategy_config.items()
        if key not in _RENDERED_STRATEGY_KEYS
    }
    logic_type = strategy_config.get(LOGIC_TYPE_KEY, '')
    if logic_type:
        rendered['decision_logic_config'] = with_configured_type(
            to_plain(DecisionLogicFactory(logger).resolve_parameters(
                logic_type, strategy_config.get('decision_logic_config', {}))),
            LOGIC_TYPE_KEY, logic_type)
    worker_factory = WorkerFactory(logger)
    worker_configs = strategy_config.get('workers', {})
    rendered['workers'] = {
        name: with_configured_type(
            to_plain(worker_factory.resolve_parameters(worker_type, worker_configs.get(name, {}))),
            WORKER_TYPE_KEY, worker_type)
        for name, worker_type in strategy_config.get('worker_instances', {}).items()
    }
    return rendered


def with_configured_type(
    parameters: Dict[str, Any],
    type_key: str,
    configured_type: str,
) -> Dict[str, Any]:
    """
    Name a component the way its configuration does, in place of the path its factory resolved.

    Args:
        parameters: A component's parameters as its factory filled them
        type_key: The key the factory injected the resolved type under
        configured_type: The type as the strategy names it — `CORE/...` or a relative file path

    Returns:
        A copy of the parameters with the configured type under the type key
    """
    projected = dict(parameters)
    projected[type_key] = configured_type
    return projected
