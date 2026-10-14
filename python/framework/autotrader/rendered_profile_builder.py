"""
Rendered profile builder (#547) — what an AutoTrader session RUNS with, as one document.

A profile file is the input to a cascade. Under it lie the `app_config.autotrader` defaults the
loader merges in, and every parameter the file leaves unset gets its schema default only when the
factory builds the component. The run-config store froze the input and nothing else, so a month in
which `app_config.json` or a default changed could prove that the configuration moved and never
say what it was. This document is the result: the merged blocks, the strategy with every default
filled in, and the broker's entry in `market_config.json` with its local override.

It is rendered BEFORE the run header is written, because the header names it and has no update
path — so it is built without constructing anything. The factories' own `resolve_parameters`
supply the defaults through the same helper their `create_*` uses, and
`assert_rendered_parameters_match` holds the two together once the components exist.

A record, never a schema: a later version of an algo may rename or drop a parameter, and an old
document stays exactly as true as it was. Nothing reads it back into a factory.
"""

from dataclasses import fields
from typing import Any, Dict, List

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.exceptions.rendered_config_errors import RenderedConfigMismatchError
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.run_config_types import RunConfigKind
from python.framework.utils.config_fingerprint_utils import to_plain
from python.framework.workers.abstract_worker import AbstractWorker

# Fields the document carries elsewhere or not at all: where the profile sits on disk is the
# SOURCE's provenance, kept in the store index under `config_id`; the strategy is rendered on its
# own, with its defaults filled in. What the session is FOR (#576) is not what it ran with: the
# header carries it.
_NOT_A_BLOCK = frozenset({'config_path', 'strategy_config', 'run_purpose'})

# The two strategy keys that are replaced by their rendered form rather than copied.
_RENDERED_STRATEGY_KEYS = frozenset({'decision_logic_config', 'workers'})


def render_autotrader_profile(config: AutoTraderConfig) -> Dict[str, Any]:
    """
    Render the configuration an AutoTrader session runs with.

    Args:
        config: The loaded profile — the `app_config` layer already merged in, the command-line
            overrides already applied

    Returns:
        The rendered document, JSON-serialisable
    """
    blocks = {
        field.name: to_plain(getattr(config, field.name))
        for field in fields(config)
        if field.name not in _NOT_A_BLOCK
    }
    return {
        'kind': RunConfigKind.AUTOTRADER_RENDERED.value,
        'blocks': blocks,
        'strategy_config': render_strategy_config(config.strategy_config),
        # The broker's own entry, local override included: `user_configs/market_config.json`
        # decides things like `dry_run` for a real account, and nothing else records them.
        'market_entry': to_plain(MarketConfigManager().get_broker_entry(config.broker_type)),
    }


def render_strategy_config(strategy_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Render a strategy as its components are built: every schema default filled in, every resolved
    type key injected.

    Only the worker instances the strategy actually runs are rendered — a `workers` entry that no
    instance names configures nothing.

    Args:
        strategy_config: The profile's `strategy_config`

    Returns:
        The rendered strategy
    """
    logger = get_global_logger()
    rendered = {
        key: to_plain(value)
        for key, value in strategy_config.items()
        if key not in _RENDERED_STRATEGY_KEYS
    }
    logic_type = strategy_config.get('decision_logic_type', '')
    if logic_type:
        rendered['decision_logic_config'] = to_plain(DecisionLogicFactory(logger).resolve_parameters(
            logic_type, strategy_config.get('decision_logic_config', {})))
    worker_factory = WorkerFactory(logger)
    worker_configs = strategy_config.get('workers', {})
    rendered['workers'] = {
        name: to_plain(worker_factory.resolve_parameters(worker_type, worker_configs.get(name, {})))
        for name, worker_type in strategy_config.get('worker_instances', {}).items()
    }
    return rendered


def assert_rendered_parameters_match(
    rendered: Dict[str, Any],
    decision_logic: AbstractDecisionLogic,
    workers: Dict[str, AbstractWorker],
) -> None:
    """
    Refuse a session whose record would misstate what its components were built with.

    Both sides come from the same factory helper, so this cannot fire on a setting — it fires on
    a defect: a construction step that changed a parameter after the factory filled it, or a
    renderer that drifted from the factory.

    Args:
        rendered: The document `render_autotrader_profile` produced for this session
        decision_logic: The constructed decision logic
        workers: The constructed workers, by instance name
    """
    strategy = rendered.get('strategy_config', {})
    difference = _first_difference(
        strategy.get('decision_logic_config', {}), to_plain(decision_logic.config))
    if difference:
        raise RenderedConfigMismatchError('the decision logic', difference)

    recorded_workers = strategy.get('workers', {})
    if set(recorded_workers) != set(workers):
        raise RenderedConfigMismatchError(
            'the worker set',
            f'recorded {sorted(recorded_workers)}, built {sorted(workers)}')
    for name, worker in workers.items():
        difference = _first_difference(recorded_workers[name], to_plain(worker.parameters))
        if difference:
            raise RenderedConfigMismatchError(f"worker '{name}'", difference)


def _first_difference(recorded: Dict[str, Any], built: Dict[str, Any]) -> str:
    """
    Name the first parameter the two sides disagree on.

    Args:
        recorded: The rendered parameters
        built: The constructed component's parameters, projected the same way

    Returns:
        A short description of the first difference, or an empty string when they agree
    """
    keys: List[str] = sorted(set(recorded) | set(built))
    for key in keys:
        if key not in built:
            return f"'{key}' is recorded but was not built"
        if key not in recorded:
            return f"'{key}' was built but is not recorded"
        if recorded[key] != built[key]:
            return f"'{key}': recorded {recorded[key]!r}, built {built[key]!r}"
    return ''
