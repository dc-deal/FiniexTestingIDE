"""
Generator exceptions.

Raised by the scenario generator when a symbol's broker config cannot authoritatively provide
the currency split needed to seed a balance (the generator never guesses — #265).
"""
from python.framework.exceptions.finiex_error import FiniexError


class SymbolCurrencyError(FiniexError, ValueError):
    """
    Raised when a symbol's broker config lacks base/quote currency or they do not match the symbol.

    Multiple inheritance with ValueError so it integrates with the existing ValueError-based
    config/data validation flow (§10).
    """
