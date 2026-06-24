"""
Pip-size derivation — the single source for turning a broker's tick / digits into the
authoritative per-symbol pip price unit. The decision logic reads it for pip-denominated
parameters (SL/TP/stop distance) and the report stamps it on each trade for exact
MAE/MFE distances, so the rule has exactly one home (replaces the per-site approximations).
"""

from python.framework.types.config_types.market_config_types import PipMode


def derive_pip_size(tick_size: float, digits: int, pip_mode: PipMode) -> float:
    """
    Authoritative per-symbol pip size from the broker tick / digits, market-type aware.

    TICK markets (crypto / others) have no pip concept — the broker tick IS the unit.
    FRACTIONAL_PIP markets (Forex) follow the pipette convention: a 5-digit (or 3-digit
    JPY) broker quotes one extra fractional digit → pip = tick * 10; a whole-pip broker
    (4-/2-digit) → pip = tick.

    Args:
        tick_size: Broker minimum price increment for the symbol
        digits: Symbol price digits (decimal places)
        pip_mode: How this market derives its pip unit (from market_config)

    Returns:
        Pip size in price space (e.g. 0.0001 for EURUSD, 0.01 for USDJPY, 0.1 for BTCUSD)
    """
    if pip_mode is PipMode.TICK:
        return tick_size
    # FRACTIONAL_PIP (Forex): pipette brokers (5-digit / 3-digit JPY) → pip = tick * 10;
    # whole-pip brokers (4-/2-digit) → pip = tick.
    return tick_size * 10 if digits in (3, 5) else tick_size
