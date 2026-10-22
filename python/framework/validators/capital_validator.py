"""
FiniexTestingIDE - Capital Validator (#489)

A bot that cannot fund a single minimum-volume order has nothing to do, and it should say so
at boot rather than at its first signal. The framework treats the whole account as the bot's
own — one account per bot — so the question is simply whether that account can pay for one
order on either side of the instrument.
"""

from typing import Dict, Optional

from python.framework.types.trading_env_types.broker_types import SymbolSpecification


def check_account_sufficiency(
    balances: Dict[str, float],
    symbol_spec: SymbolSpecification,
    reference_price: Optional[float],
) -> Optional[str]:
    """
    Refuse a boot whose account can fund no order at all, on either side.

    The criterion is deliberately the AND of both sides: a spot bot may legitimately start
    holding only the BASE asset and open by selling — the Field Study funds both sides on
    purpose — so an empty quote balance alone is not a reason to refuse. Only an account that
    can neither buy nor sell the instrument's minimum volume has nothing this bot could ever do.

    The buy side needs a price and the sell side does not: `volume_min` is quoted in lots —
    base units at spot — so it compares directly against the base balance, while the quote a purchase costs
    is `volume_min * price`. A venue's minimum order VALUE (`cost_min`, in the quote) raises the
    smallest acceptable order to `cost_min / price` where that is larger — on both sides, and
    only where a price exists to convert it with. Where no price is available the buy side is UNKNOWN, and an
    unknown side can never complete the AND — the boot proceeds, and the caller says out loud
    that the check could not run (#489). Guessing a price here would refuse a session over an
    invented number.

    Args:
        balances: The account's assets as resolved at boot, currency → amount
        symbol_spec: The instrument's specification, for its two minimums and its two currencies
        reference_price: A price to value the minimum purchase at, or None when the boot has
            none — no tick has arrived yet and warmup may not have run

    Returns:
        The failure message, or None when the account can fund at least one order
    """
    base_balance = balances.get(symbol_spec.base_currency, 0.0)
    quote_balance = balances.get(symbol_spec.quote_currency, 0.0)
    priced = reference_price is not None and reference_price > 0

    # Spot arithmetic: a lot is one base unit and a sell needs the holding. On margin a lot is
    # `contract_size` units and either side needs margin, so this check can pass an MT5 account
    # its first order is refused for (2026-10-18, #209).
    # The smallest order the venue accepts, in base units: the minimum volume, or more where the
    # minimum order value asks for more at this price
    minimum_lots = symbol_spec.volume_min
    if priced and symbol_spec.cost_min > 0:
        minimum_lots = max(minimum_lots, symbol_spec.cost_min / reference_price)

    can_sell = base_balance >= minimum_lots
    if can_sell:
        return None

    if not priced:
        # The buy side cannot be judged, so nothing can be concluded. The caller reports it.
        return None

    minimum_cost = minimum_lots * reference_price
    if quote_balance >= minimum_cost:
        return None
    value_note = (
        f' (the venue accepts no order worth less than {symbol_spec.cost_min:.8f} '
        f'{symbol_spec.quote_currency})'
        if minimum_lots > symbol_spec.volume_min else '')

    # Fixed 8 decimals throughout: a minimum volume is routinely small enough that `:g`
    # renders it as `5e-05`, and an operator reading a refusal should not have to decode
    # scientific notation. Eight is the granularity every supported venue settles in.
    return (
        f'Capital: the account can fund no order on {symbol_spec.symbol}. '
        f'A minimum-volume BUY of {minimum_lots:.8f} '
        f'{symbol_spec.base_currency} costs ~{minimum_cost:.8f} '
        f'{symbol_spec.quote_currency} at {reference_price:.8f}{value_note}, and the account holds '
        f'{quote_balance:.8f} {symbol_spec.quote_currency}. A minimum-volume SELL needs '
        f'{minimum_lots:.8f} {symbol_spec.base_currency}, and the account holds '
        f'{base_balance:.8f}. This bot could never place an order — fund the account, or '
        f'run an instrument with a smaller minimum volume.'
    )


def describe_missing_reference_price(symbol: str) -> str:
    """
    Say that the sufficiency check ran without a price, so its buy side was not judged.

    Spoken rather than swallowed: a check that silently does not run reads exactly like one
    that ran and passed, and the operator would carry a boot guard that guards nothing.

    Args:
        symbol: The instrument the check was for

    Returns:
        The message for the session channel
    """
    return (
        f'⚠️ Capital: the minimum-order sufficiency check for {symbol} ran without a '
        f'reference price — no warmup bars were available, so only the SELL side was '
        f'judged. A quote balance too small for one minimum-volume BUY would not have been '
        f'caught here; the first order attempt will refuse it instead.'
    )
