"""
Account currency types — what the one account-currency rule decides for an account (#615).
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AccountCurrencyResolution:
    """
    The currency one account is accounted in, as `resolve_account_currency` decides it.

    Args:
        account_currency: The resolved currency; None when the configuration leaves it ambiguous
            — several balances, none of them in the symbol's base or quote currency
        normalized_from: The currency a spot account would otherwise have used, when the rule
            replaced it by the quote currency; None when nothing was replaced
    """
    account_currency: Optional[str]
    normalized_from: Optional[str] = None
