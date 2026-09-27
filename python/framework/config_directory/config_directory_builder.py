"""
FiniexTestingIDE - Config Directory Builder (#554)

Reads ONE configuration file into a directory row — from its raw JSON, never through the loader.

Why not the loader: the loader validates, merges the app-config cascade, creates directories and
logs, and a listing must do none of that — it is read on every page load and a file being edited
is broken for minutes at a time. So this reads what a file DECLARES and says so honestly: a row is
`readable` or `unreadable`, never "valid". Validation stays where it has always been, at run start.

The one rule it shares with the loader is the per-scenario strategy cascade, and it is shared by
calling the loader's own `ScenarioCascade.merge_strategy_config` rather than by restating it — so
the decision logic and the workers a row names are the ones a run would resolve.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.api.directory_types import DirectoryRow, DirectoryScenario
from python.framework.types.config_directory_types import (
    ConfigKind,
    ConfigReadStatus,
    DiscoveredConfigFile,
)
from python.scenario.scenario_cascade import ScenarioCascade

# The key that makes a JSON file a scenario set — the one the scenario-set finder always used.
SCENARIO_SET_MARKER = 'scenario_set_name'
# The keys that make one an AutoTrader profile — the rule the bot-id collision check applies.
PROFILE_MARKERS = frozenset({'symbol', 'broker_type', 'strategy_config'})

# What a broker the market configuration does not know is shown as — a row, never a crash.
UNKNOWN_MARKET_TYPE = 'unknown'

# The adapter a profile runs when it names none — the AutoTrader loader's own default.
_DEFAULT_ADAPTER_TYPE = 'mock'

MarketTypeOf = Callable[[str], str]


class ConfigFileUnreadable(Exception):
    """A file that carries a marker but cannot be read as the configuration it claims to be."""


def market_type_lookup() -> MarketTypeOf:
    """
    A memoized broker → market type lookup that answers `unknown` instead of raising.

    Returns:
        The lookup
    """
    market_config = MarketConfigManager()
    known: Dict[str, str] = {}

    def lookup(broker_type: str) -> str:
        if broker_type not in known:
            try:
                known[broker_type] = market_config.get_market_type(broker_type).value
            except (ValueError, KeyError):
                known[broker_type] = UNKNOWN_MARKET_TYPE
        return known[broker_type]

    return lookup


def read_config_file(candidate: DiscoveredConfigFile, market_type_of: MarketTypeOf) -> DirectoryRow:
    """
    What one file declares, as a directory row without its run figures.

    Args:
        candidate: The file, as the walk found it
        market_type_of: Broker type → market type

    Returns:
        The row; `not_a_config` for JSON carrying neither marker, `unreadable` with the reason
        for a file that cannot be parsed or has a marker with the wrong shape
    """
    base = dict(file=candidate.path.name, origin=candidate.origin, folder=candidate.folder,
                modified_at=_iso(candidate.mtime))
    try:
        data = _load(candidate.path)
        if not isinstance(data, dict):
            return DirectoryRow(status=ConfigReadStatus.NOT_A_CONFIG, **base)
        if SCENARIO_SET_MARKER in data:
            return DirectoryRow(status=ConfigReadStatus.READABLE, kind=ConfigKind.SCENARIO_SET,
                                **base, **_scenario_set_fields(data, market_type_of))
        if PROFILE_MARKERS <= data.keys():
            return DirectoryRow(status=ConfigReadStatus.READABLE,
                                kind=ConfigKind.AUTOTRADER_PROFILE,
                                **base, **_profile_fields(data, market_type_of))
        return DirectoryRow(status=ConfigReadStatus.NOT_A_CONFIG, **base)
    except ConfigFileUnreadable as error:
        return DirectoryRow(status=ConfigReadStatus.UNREADABLE, reason=str(error), **base)


def read_scenarios(path: Path, market_type_of: MarketTypeOf) -> List[DirectoryScenario]:
    """
    A scenario set's scenarios, read fresh from its file, each after the strategy cascade.

    Args:
        path: The scenario set file
        market_type_of: Broker type → market type

    Returns:
        One entry per scenario the file names, disabled ones included; empty when the file is
        not a readable scenario set
    """
    try:
        data = _load(path)
        if not isinstance(data, dict) or SCENARIO_SET_MARKER not in data:
            return []
        global_strategy, scenarios = _scenario_parts(data)
    except ConfigFileUnreadable:
        return []
    entries = []
    for scenario, strategy in ((s, _merged_strategy(global_strategy, s)) for s in scenarios):
        broker_type = _text(scenario.get('data_broker_type'))
        max_ticks = scenario.get('max_ticks')
        entries.append(DirectoryScenario(
            name=_text(scenario.get('name')),
            symbol=_text(scenario.get('symbol')),
            broker_type=broker_type,
            market_type=market_type_of(broker_type) if broker_type else '',
            start=_text(scenario.get('start_date')),
            end=_text(scenario.get('end_date')),
            max_ticks=max_ticks if isinstance(max_ticks, int) else None,
            enabled=bool(scenario.get('enabled', True)),
            decision_logic=_text(strategy.get('decision_logic_type')),
        ))
    return entries


def _scenario_set_fields(data: Dict[str, Any], market_type_of: MarketTypeOf) -> Dict[str, Any]:
    """
    The row fields a scenario set declares.

    Args:
        data: The parsed file
        market_type_of: Broker type → market type

    Returns:
        The fields, computed over the ENABLED scenarios — the ones a run would execute
    """
    name = data[SCENARIO_SET_MARKER]
    if not isinstance(name, str):
        raise ConfigFileUnreadable(f'`{SCENARIO_SET_MARKER}` is not a string')
    global_strategy, scenarios = _scenario_parts(data)
    enabled = [scenario for scenario in scenarios if scenario.get('enabled', True)]
    strategies = [_merged_strategy(global_strategy, scenario) for scenario in enabled]
    broker_types = _distinct(scenario.get('data_broker_type') for scenario in enabled)
    return dict(
        name=name,
        scenarios_declared=len(scenarios),
        scenarios_enabled=len(enabled),
        symbols=_distinct(scenario.get('symbol') for scenario in enabled),
        broker_types=broker_types,
        market_types=_distinct(market_type_of(broker) for broker in broker_types),
        decision_logics=_distinct(strategy.get('decision_logic_type') for strategy in strategies),
        workers=_distinct(worker for strategy in strategies
                          for worker in _worker_types(strategy)),
    )


def _profile_fields(data: Dict[str, Any], market_type_of: MarketTypeOf) -> Dict[str, Any]:
    """
    The row fields an AutoTrader profile declares — one unit, the session.

    Args:
        data: The parsed file
        market_type_of: Broker type → market type

    Returns:
        The fields
    """
    strategy = data['strategy_config']
    if not isinstance(strategy, dict):
        raise ConfigFileUnreadable('`strategy_config` is not an object')
    symbol, broker_type = data['symbol'], data['broker_type']
    if not isinstance(symbol, str) or not isinstance(broker_type, str):
        raise ConfigFileUnreadable('`symbol` and `broker_type` must be strings')
    dry_run = data.get('dry_run')
    return dict(
        name=_text(data.get('name')),
        scenarios_declared=1,
        scenarios_enabled=1,
        symbols=[symbol],
        broker_types=[broker_type],
        market_types=[market_type_of(broker_type)],
        decision_logics=_distinct([strategy.get('decision_logic_type')]),
        workers=_distinct(_worker_types(strategy)),
        bot_id=_text(data.get('bot_id')),
        adapter_type=_text(data.get('adapter_type')) or _DEFAULT_ADAPTER_TYPE,
        dry_run_declared=dry_run if isinstance(dry_run, bool) else None,
    )


def _scenario_parts(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    A scenario set's global strategy and its scenarios, shape-checked.

    Args:
        data: The parsed file

    Returns:
        (global strategy_config, scenarios)
    """
    global_block = data.get('global') or {}
    if not isinstance(global_block, dict):
        raise ConfigFileUnreadable('`global` is not an object')
    global_strategy = global_block.get('strategy_config') or {}
    if not isinstance(global_strategy, dict):
        raise ConfigFileUnreadable('`global.strategy_config` is not an object')
    scenarios = data.get('scenarios') or []
    if not isinstance(scenarios, list) or not all(isinstance(s, dict) for s in scenarios):
        raise ConfigFileUnreadable('`scenarios` is not a list of objects')
    return global_strategy, scenarios


def _merged_strategy(global_strategy: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
    """
    One scenario's strategy after the loader's own cascade.

    Args:
        global_strategy: The set's global strategy_config
        scenario: The scenario

    Returns:
        The merged strategy_config
    """
    override = scenario.get('strategy_config') or {}
    if not isinstance(override, dict):
        raise ConfigFileUnreadable(f"scenario '{scenario.get('name')}': `strategy_config` is "
                                   f'not an object')
    return ScenarioCascade.merge_strategy_config(global_strategy, override)


def _worker_types(strategy: Dict[str, Any]) -> List[str]:
    """
    The worker types a strategy instantiates.

    Args:
        strategy: A strategy_config

    Returns:
        The types named in its `worker_instances`
    """
    instances = strategy.get('worker_instances') or {}
    if not isinstance(instances, dict):
        raise ConfigFileUnreadable('`worker_instances` is not an object')
    return [worker for worker in instances.values() if isinstance(worker, str)]


def _load(path: Path) -> Any:
    """
    One file's JSON.

    Args:
        path: The file

    Returns:
        The parsed content
    """
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ConfigFileUnreadable(f'line {error.lineno}: {error.msg}') from error
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigFileUnreadable(str(error)) from error


def _distinct(values) -> List[str]:
    """
    The distinct non-empty strings among some values, sorted.

    Args:
        values: Any iterable

    Returns:
        The sorted distinct strings
    """
    return sorted({value for value in values if isinstance(value, str) and value})


def _text(value: Optional[Any]) -> str:
    """
    A declared value as text, '' when absent.

    Args:
        value: The value

    Returns:
        Its string form
    """
    return '' if value is None else str(value)


def _iso(mtime: float) -> str:
    """
    A modification time as ISO-8601 UTC.

    Args:
        mtime: Seconds since the epoch

    Returns:
        The timestamp
    """
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
