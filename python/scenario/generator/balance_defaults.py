"""
Generator balance defaults — ensure a generated scenario set runs out of the box.

Cascade-capable keys are no longer emitted per scenario, and the validator requires a balance
matching the symbol. So every generation path seeds a starting balance in the symbol's **quote**
currency (forex/crypto settle in the quote). The quote is resolved AUTHORITATIVELY from the
broker config (never guessed from the symbol string — #265); a missing or inconsistent currency
split is a hard `SymbolCurrencyError` (FiniexError). One place owns the resolution + the default
amount, so all generators behave identically.
"""
from typing import Any, Dict, Optional, Tuple

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.exceptions.generator_errors import SymbolCurrencyError
from python.framework.factory.broker_config_factory import BrokerConfigFactory

# Default starting balance for the symbol's quote currency (operator-tunable in the generated file).
DEFAULT_QUOTE_BALANCE = 10000.0


def resolve_symbol_currencies(symbol: str, broker_type: str) -> Tuple[str, str]:
    """
    Resolve (base, quote) for a symbol authoritatively from the broker config.

    Loads the broker config via the market config (`broker_config_path`) and reads the symbol's
    `base_currency` / `quote_currency`. Raises `SymbolCurrencyError` when the symbol is unknown,
    its currency split is missing, or it does not match the symbol key.

    Args:
        symbol: Trading symbol (e.g. 'DOTUSD')
        broker_type: Broker type whose config holds the symbol spec (e.g. 'kraken_spot')

    Returns:
        Tuple of (base_currency, quote_currency)
    """
    config_path = MarketConfigManager().get_broker_config_path(broker_type)
    broker_config = BrokerConfigFactory.build_broker_config(config_path)
    try:
        spec = broker_config.get_symbol_specification(symbol)
    except ValueError as e:
        raise SymbolCurrencyError(
            f"Symbol '{symbol}' not found in broker config for '{broker_type}' "
            f"({config_path}) — cannot resolve its currency split.") from e

    _validate_symbol_currencies(symbol, spec.base_currency, spec.quote_currency, broker_type)
    return spec.base_currency, spec.quote_currency


def resolve_quote_currency(symbol: str, broker_type: str) -> str:
    """
    The quote currency of a symbol, resolved authoritatively from the broker config.

    Args:
        symbol: Trading symbol
        broker_type: Broker type whose config holds the symbol spec

    Returns:
        The quote currency (raises SymbolCurrencyError on a missing / inconsistent split)
    """
    return resolve_symbol_currencies(symbol, broker_type)[1]


def _validate_symbol_currencies(symbol: str, base: str, quote: str, broker_type: str) -> None:
    """Guard: base + quote are present AND concatenate to the symbol key; raise otherwise."""
    if not base or not quote:
        raise SymbolCurrencyError(
            f"Symbol '{symbol}' in broker '{broker_type}' has no base_currency/quote_currency "
            f"(base='{base}', quote='{quote}'). Fix the symbol entry in the broker config.")
    if f"{base}{quote}".upper() != symbol.upper():
        raise SymbolCurrencyError(
            f"Symbol '{symbol}' in broker '{broker_type}' does not match its currency split — "
            f"base '{base}' + quote '{quote}' = '{base}{quote}', expected '{symbol}'.")


def ensure_quote_balance(
    trade_simulator_config: Optional[Dict[str, Any]], quote_currency: str
) -> Dict[str, Any]:
    """
    Return a trade_simulator_config that carries a balance in the given quote currency.

    Does not clobber an existing balance for that currency (an operator-provided amount wins).

    Args:
        trade_simulator_config: The existing config (may be None / empty)
        quote_currency: The (already resolved) quote currency to seed

    Returns:
        A new dict with `balances[<quote>]` ensured
    """
    config: Dict[str, Any] = dict(trade_simulator_config or {})
    balances: Dict[str, float] = dict(config.get('balances', {}))
    balances.setdefault(quote_currency, DEFAULT_QUOTE_BALANCE)
    config['balances'] = balances
    return config
