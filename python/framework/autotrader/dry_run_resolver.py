"""
FiniexTestingIDE - Dry-Run Resolver

The ONE answer to whether an AutoTrader session places real orders. The broker setup arms the
adapter with it and the session reads it everywhere else; before it existed the two resolved the
same flag by different rules, so a profile could arm a real adapter that the session then refused.
"""

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.exceptions.live_execution_errors import DryRunConflictError
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig


def resolve_dry_run(config: AutoTraderConfig) -> bool:
    """
    Whether this session simulates order execution instead of placing real orders.

    A mock adapter is always dry. Otherwise the broker's market_config setting applies, which a
    profile may TIGHTEN but never loosen: `dry_run: true` in the profile wins over a live broker
    default, while `dry_run: false` against a dry-run broker default is refused rather than
    honoured or ignored.

    The asymmetry is deliberate. A profile is a per-run file that gets copied and edited; the
    broker setting is the operator's standing posture. Letting a profile switch real money ON
    would put that decision in the most easily-shared place, and silently ignoring the attempt
    would leave a file claiming a safety it does not have — which is exactly how a profile marked
    `dry_run: true` was read as an observation run while the broker default said otherwise.

    Args:
        config: The session's AutoTrader configuration

    Returns:
        True if the session must not place real orders
    """
    if config.adapter_type == 'mock':
        return True
    broker_default = MarketConfigManager().get_dry_run(config.broker_type)
    profile_override = config.dry_run
    if profile_override is None:
        return broker_default
    if broker_default and not profile_override:
        raise DryRunConflictError(
            f"Profile '{config.profile_name}' sets dry_run=false, but "
            f"market_config.json has dry_run=true for broker "
            f"'{config.broker_type}'. A profile may only tighten the dry-run "
            f"posture, never loosen it — enabling real orders is a deliberate change "
            f"to market_config.json (or its user_configs override)."
        )
    return profile_override
