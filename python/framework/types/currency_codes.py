"""
FiniexTestingIDE - Currency Codes
ISO 4217 currency codes with symbol mapping

Contains:
- CURRENCY_SYMBOLS: Mapping of currency codes to display symbols
- Covers major forex currencies, minors, and common exotics

Usage:
    from python.framework.types.currency_codes import CURRENCY_SYMBOLS
    
    symbol = CURRENCY_SYMBOLS.get("USD", "USD")  # Returns "$"
    symbol = CURRENCY_SYMBOLS.get("ZAR", "ZAR")  # Returns "ZAR" (fallback)
"""

# ============================================================================
# CURRENCY SYMBOL MAPPING (ISO 4217)
# ============================================================================

CURRENCY_SYMBOLS = {
    # === MAJOR CURRENCIES (G10) ===
    "USD": "$",      # United States Dollar
    "EUR": "€",      # Euro
    "GBP": "£",      # British Pound Sterling
    "JPY": "¥",      # Japanese Yen
    "CHF": "CHF",    # Swiss Franc (no unique symbol)
    "CAD": "C$",     # Canadian Dollar
    "AUD": "A$",     # Australian Dollar
    "NZD": "NZ$",    # New Zealand Dollar

    # === SCANDINAVIAN CURRENCIES ===
    "SEK": "kr",     # Swedish Krona
    "NOK": "kr",     # Norwegian Krone
    "DKK": "kr",     # Danish Krone

    # === ASIAN CURRENCIES ===
    "CNY": "¥",      # Chinese Yuan Renminbi
    "HKD": "HK$",    # Hong Kong Dollar
    "SGD": "S$",     # Singapore Dollar
    "KRW": "₩",      # South Korean Won
    "TWD": "NT$",    # Taiwan Dollar
    "THB": "฿",      # Thai Baht
    "INR": "₹",      # Indian Rupee
    "IDR": "Rp",     # Indonesian Rupiah
    "MYR": "RM",     # Malaysian Ringgit
    "PHP": "₱",      # Philippine Peso
    "VND": "₫",      # Vietnamese Dong

    # === CENTRAL/EASTERN EUROPEAN CURRENCIES ===
    "PLN": "zł",     # Polish Zloty
    "CZK": "Kč",     # Czech Koruna
    "HUF": "Ft",     # Hungarian Forint
    "RON": "lei",    # Romanian Leu
    "RUB": "₽",      # Russian Ruble
    "TRY": "₺",      # Turkish Lira

    # === MIDDLE EASTERN CURRENCIES ===
    "ILS": "₪",      # Israeli New Shekel
    "SAR": "﷼",      # Saudi Riyal
    "AED": "د.إ",    # UAE Dirham
    "KWD": "د.ك",    # Kuwaiti Dinar

    # === LATIN AMERICAN CURRENCIES ===
    "MXN": "Mex$",   # Mexican Peso
    "BRL": "R$",     # Brazilian Real
    "ARS": "AR$",    # Argentine Peso
    "CLP": "CLP$",   # Chilean Peso
    "COP": "COL$",   # Colombian Peso
    "PEN": "S/",     # Peruvian Sol

    # === AFRICAN CURRENCIES ===
    "ZAR": "R",      # South African Rand
    "EGP": "E£",     # Egyptian Pound
    "NGN": "₦",      # Nigerian Naira
    "KES": "KSh",    # Kenyan Shilling

    # === OTHER IMPORTANT FOREX CURRENCIES ===
    "XAU": "XAU",    # Gold (Troy Ounce)
    "XAG": "XAG",    # Silver (Troy Ounce)
    "BTC": "₿",      # Bitcoin (if trading crypto CFDs)
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_currency_symbol(currency_code: str) -> str:
    """
    Get currency symbol for given ISO 4217 code.

    Falls back to currency code if symbol not found.

    Args:
        currency_code: ISO 4217 currency code (e.g., "USD", "EUR")

    Returns:
        Currency symbol (e.g., "$", "€") or code if not found

    Examples:
        >>> get_currency_symbol("USD")
        '$'
        >>> get_currency_symbol("EUR")
        '€'
        >>> get_currency_symbol("ZWL")  # Zimbabwe Dollar (not in mapping)
        'ZWL'
    """
    return CURRENCY_SYMBOLS.get(currency_code.upper(), currency_code)


def has_currency_symbol(currency_code: str) -> bool:
    """
    Check if currency has a dedicated symbol in mapping.

    Args:
        currency_code: ISO 4217 currency code

    Returns:
        True if currency has symbol, False otherwise

    Examples:
        >>> has_currency_symbol("USD")
        True
        >>> has_currency_symbol("ZWL")
        False
    """
    return currency_code.upper() in CURRENCY_SYMBOLS


def format_currency_simple(amount: float, currency_code: str) -> str:
    """
    Simple currency formatter with symbol.

    Uses symbol if available, otherwise currency code.
    Symbol is placed before amount with no space.

    Args:
        amount: Monetary amount
        currency_code: ISO 4217 currency code

    Returns:
        Formatted string (e.g., "$100.50", "EUR 100.50")

    Examples:
        >>> format_currency_simple(100.50, "USD")
        '$100.50'
        >>> format_currency_simple(100.50, "EUR")
        '€100.50'
        >>> format_currency_simple(100.50, "ZAR")
        'R100.50'
        >>> format_currency_simple(100.50, "XYZ")  # Unknown
        'XYZ 100.50'
    """
    symbol = get_currency_symbol(currency_code)

    # If symbol is same as code (unknown currency), add space
    if symbol == currency_code:
        return f"{symbol} {amount:.2f}"
    else:
        return f"{symbol}{amount:.2f}"
