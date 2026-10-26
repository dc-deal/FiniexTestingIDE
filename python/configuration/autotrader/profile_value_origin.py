"""
FiniexTestingIDE - Profile Value Origin (#626)

Where a value of an AutoTrader session's rendered configuration comes from now: the profile, the
AutoTrader base layer of `app_config.json` as shipped or as the workspace overrides it, the broker's
entry in `market_config.json` likewise, or a default no file sets. A start that refuses a changed
configuration names it beside every changed value, so the operator knows where to undo the change —
a release that moved a shipped default reads differently from an edited profile.

Only the value the session runs with NOW can be placed: the layers an earlier session read are not
frozen, only the rendered result it ran with.
"""

import json
from typing import Any, Dict, Iterable, List

from python.configuration.config_file_loader import ConfigFileLoader
from python.configuration.market_config_loader import MarketConfigFileLoader
from python.framework.types.composite_types import ValueOrigin

# Values the start works out itself rather than reads from one place
_RESOLVED_SETTINGS = frozenset({'account_currency', 'ticks_from', 'orders_to'})
# The rendered profile's sections that no app_config layer sets
_MARKET_ENTRY = 'market_entry'
_STRATEGY = 'strategy_config'
# Where app_config.json keeps the AutoTrader base layer
_APP_SECTION = 'autotrader'


def value_origins(
    settings: Iterable[str],
    profile_path: str,
    broker_type: str,
) -> Dict[str, ValueOrigin]:
    """
    Where each of the given values of a session's rendered profile comes from now.

    Args:
        settings: Paths in the rendered profile, without `blocks.`
        profile_path: The profile file
        broker_type: The broker whose entry in `market_config.json` the session uses

    Returns:
        Setting → its origin
    """
    with open(profile_path, 'r') as f:
        profile = json.load(f)
    app_shipped, app_workspace = ConfigFileLoader.get_layers()
    market_shipped, market_workspace = MarketConfigFileLoader.get_layers()
    layers = {
        ValueOrigin.PROFILE: profile,
        ValueOrigin.APP_CONFIG_WORKSPACE: app_workspace.get(_APP_SECTION) or {},
        ValueOrigin.APP_CONFIG: app_shipped.get(_APP_SECTION) or {},
        ValueOrigin.MARKET_CONFIG_WORKSPACE: _broker_entry(market_workspace, broker_type),
        ValueOrigin.MARKET_CONFIG: _broker_entry(market_shipped, broker_type),
    }
    return {setting: _origin_of(setting, layers) for setting in settings}


def _origin_of(setting: str, layers: Dict[ValueOrigin, Dict[str, Any]]) -> ValueOrigin:
    """
    The layer that sets one value — the one that wins the merge.

    Args:
        setting: A path in the rendered profile, without `blocks.`
        layers: Each file layer as written, by the origin it stands for

    Returns:
        Its origin
    """
    if setting in _RESOLVED_SETTINGS:
        return ValueOrigin.RESOLVED
    path = setting.split('.')
    if path[0] == _MARKET_ENTRY:
        for origin in (ValueOrigin.MARKET_CONFIG_WORKSPACE, ValueOrigin.MARKET_CONFIG):
            if _holds(layers[origin], path[1:]):
                return origin
        return ValueOrigin.CODE_DEFAULT
    if _holds(layers[ValueOrigin.PROFILE], path):
        return ValueOrigin.PROFILE
    if path[0] == _STRATEGY:
        return ValueOrigin.CODE_DEFAULT
    for origin in (ValueOrigin.APP_CONFIG_WORKSPACE, ValueOrigin.APP_CONFIG):
        if _holds(layers[origin], path):
            return origin
    return ValueOrigin.CODE_DEFAULT


def _broker_entry(market_config: Dict[str, Any], broker_type: str) -> Dict[str, Any]:
    """
    One broker's entry in a market configuration document.

    Args:
        market_config: The document — shipped or the workspace override
        broker_type: The broker

    Returns:
        Its entry; empty when the document does not list it
    """
    brokers: List[Any] = market_config.get('brokers') or []
    for entry in brokers:
        if isinstance(entry, dict) and entry.get('broker_type') == broker_type:
            return entry
    return {}


def _holds(node: Any, path: List[str]) -> bool:
    """
    Whether a document sets the value at a path.

    Args:
        node: The document
        path: The keys down to the value

    Returns:
        True when every key on the way exists
    """
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True
