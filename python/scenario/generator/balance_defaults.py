"""
Generator balance defaults — ensure a generated scenario set runs out of the box.

Cascade-capable keys are no longer emitted per scenario, and the validator requires a balance
matching the symbol. So every generation path seeds a starting balance in the symbol's **quote**
currency (forex/crypto settle in the quote). One place owns the quote source + the default amount,
so all generators behave identically.
"""
from typing import Any, Dict, Optional

from python.framework.validators.scenario_validator import ScenarioValidator

# Default starting balance for the symbol's quote currency (operator-tunable in the generated file).
DEFAULT_QUOTE_BALANCE = 10000.0


def ensure_quote_balance(
    trade_simulator_config: Optional[Dict[str, Any]], symbol: str
) -> Dict[str, Any]:
    """
    Return a trade_simulator_config that carries a balance in the symbol's quote currency.

    Does not clobber an existing balance for that currency (an operator-provided amount wins).
    The quote currency is resolved via the canonical `ScenarioValidator.detect_quote_currency`.

    Args:
        trade_simulator_config: The existing config (may be None / empty)
        symbol: Trading symbol whose quote currency seeds the balance

    Returns:
        A new dict with `balances[<quote>]` ensured
    """
    config: Dict[str, Any] = dict(trade_simulator_config or {})
    balances: Dict[str, float] = dict(config.get('balances', {}))
    quote_currency = ScenarioValidator.detect_quote_currency(symbol)
    balances.setdefault(quote_currency, DEFAULT_QUOTE_BALANCE)
    config['balances'] = balances
    return config
