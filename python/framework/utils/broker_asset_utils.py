"""
FiniexTestingIDE - Broker Asset Code Normalization

A venue reports balances under its own asset codes, and they do not match the currency codes
a symbol specification speaks in. Comparing the two without normalising is how a held coin
reads as zero: 'XXBT' is not 'BTC' to a string comparison, and the difference is silent.

Kraken-shaped today (the legacy X/Z prefixes and the XBT alias) and framework-side on purpose:
the reconciler and the cold-start book check must answer the same way, so the rule cannot live
inside one of them. When a second venue needs a different rule, it contributes it here — the
same split §43 draws for the connection ladder.
"""


def normalize_broker_asset(code: str) -> str:
    """
    Normalize a broker asset code to a standard currency code.

    Handles Kraken's legacy prefixes (X for crypto, Z for fiat on 4-char codes) and the
    XBT→BTC alias. Best-effort; validated against the real API by the Field Study (#332)
    and the live-adapter tests.

    Args:
        code: Broker asset code (e.g. 'ZUSD', 'XETH', 'XXBT')

    Returns:
        Standard currency code (e.g. 'USD', 'ETH', 'BTC')
    """
    if len(code) == 4 and code[0] in ('X', 'Z'):
        code = code[1:]
    return 'BTC' if code == 'XBT' else code
