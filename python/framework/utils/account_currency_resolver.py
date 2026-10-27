"""
The account currency of one account — one rule for the backtest and the AutoTrader session (#615).

The rule existed twice, and the copies differed at spot: the backtest normalised a currency other
than the quote to the quote, a session kept it. One profile run as a session and as a backtest
could then account in two currencies — two composite keys for one strategy, and a session's P&L in
the base asset beside its fees in the quote. Both pipelines now ask this module.
"""

from typing import Dict, Optional

from python.framework.types.account_currency_types import AccountCurrencyResolution
from python.framework.types.config_types.market_config_types import TradingModel


def resolve_account_currency(
    explicit: Optional[str],
    balances: Dict[str, float],
    base: str,
    quote: str,
    trading_model: TradingModel,
) -> AccountCurrencyResolution:
    """
    Resolve the currency an account is accounted in.

    A configured currency wins; otherwise the quote currency when the balances hold it, then the
    base, then the only balance there is. A spot account settles in the quote currency whatever was
    chosen — a base-denominated account would book its gross P&L in the base asset and its fees in
    the quote — so the choice is replaced there, and the resolution says what it replaced.

    Args:
        explicit: The configured account currency; None or '' when none is configured
        balances: The account's starting balances by currency
        base: The symbol's base currency
        quote: The symbol's quote currency
        trading_model: The broker's trading model

    Returns:
        The resolution; its currency is None when several balances name neither base nor quote
    """
    if explicit:
        chosen = explicit
    elif quote in balances:
        chosen = quote
    elif base in balances:
        chosen = base
    elif len(balances) == 1:
        chosen = next(iter(balances))
    else:
        return AccountCurrencyResolution(account_currency=None)
    if trading_model == TradingModel.SPOT and chosen != quote:
        return AccountCurrencyResolution(account_currency=quote, normalized_from=chosen)
    return AccountCurrencyResolution(account_currency=chosen)
