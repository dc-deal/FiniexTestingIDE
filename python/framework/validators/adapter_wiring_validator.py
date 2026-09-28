"""
FiniexTestingIDE - Adapter Wiring Validator

Three keys shape an AutoTrader session and nothing held them together: `adapter_type` picks the
broker adapter, `tick_source.type` the feed, and the presence of `scenario_settings` the replayed
data package. Every shipped profile pairs them — mock with mock, live with a venue feed — but the
loader accepted any mix, and the documentation even advertised a live adapter on replayed ticks.
"""

from python.framework.exceptions.live_execution_errors import AdapterWiringError
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig

# The tick source that replays archived data; every other type reads a venue. Public, because the
# run header answers "where did the ticks come from" by the same rule.
REPLAY_TICK_SOURCE = 'mock'


def refuse_live_adapter_on_replayed_ticks(config: AutoTraderConfig) -> None:
    """
    Refuse a real adapter whose ticks would not come from its venue.

    The reverse pairing — a mock adapter on a real feed — places nothing at any venue and stays
    allowed: it is how a feed is watched without trading.

    Args:
        config: The loaded AutoTrader configuration
    """
    if config.adapter_type != 'live':
        return
    reasons = []
    if config.tick_source.type == REPLAY_TICK_SOURCE:
        reasons.append(f"tick_source.type is '{REPLAY_TICK_SOURCE}' (replayed ticks)")
    if config.scenario_settings is not None:
        reasons.append('scenario_settings is present (a replayed data window)')
    if not reasons:
        return
    raise AdapterWiringError(
        f"{config.config_path}: adapter_type is 'live', but {' and '.join(reasons)}. A live "
        'adapter trades at the real market, so its ticks must come from that venue: decisions '
        'on replayed history would become real orders once dry_run is off. Use adapter_type '
        "'mock' to replay a window, or a venue tick source without scenario_settings to trade.")
