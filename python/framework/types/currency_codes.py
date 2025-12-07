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
# NUMBER FORMAT TEMPLATES (REUSABLE)
# ============================================================================

FMT_EN = {"thousands": ",", "decimal": "."}
FMT_DE = {"thousands": ".", "decimal": ","}
FMT_CH = {"thousands": "'", "decimal": "."}
FMT_NONE = {"thousands": "", "decimal": "."}  # fallback / simple


# ============================================================================
# CURRENCY -> NUMBER FORMAT MAPPING
# ============================================================================

CURRENCY_FORMATS = {
    # Anglo style
    "USD": FMT_EN,
    "GBP": FMT_EN,
    "CAD": FMT_EN,
    "AUD": FMT_EN,
    "NZD": FMT_EN,
    "HKD": FMT_EN,
    "SGD": FMT_EN,
    "MXN": FMT_EN,
    "ZAR": FMT_EN,

    # Euro / European style
    "EUR": FMT_DE,
    "PLN": FMT_DE,
    "CZK": FMT_DE,
    "HUF": FMT_DE,
    "RON": FMT_DE,
    "TRY": FMT_DE,
    "RUB": FMT_DE,

    # Swiss
    "CHF": FMT_CH,

    # Asian mostly English style
    "JPY": FMT_EN,
    "CNY": FMT_EN,
    "KRW": FMT_EN,
    "INR": FMT_EN,
    "IDR": FMT_EN,
    "MYR": FMT_EN,
    "PHP": FMT_EN,
    "THB": FMT_EN,
    "TWD": FMT_EN,

    # Middle East
    "ILS": FMT_EN,
    "SAR": FMT_EN,
    "AED": FMT_EN,
    "KWD": FMT_EN,

    # Latin America
    "BRL": FMT_DE,
    "ARS": FMT_DE,
    "CLP": FMT_DE,
    "COP": FMT_DE,
    "PEN": FMT_DE,

    # Africa
    "EGP": FMT_EN,
    "NGN": FMT_EN,
    "KES": FMT_EN,

    # Commodities / Crypto
    "XAU": FMT_EN,
    "XAG": FMT_EN,
    "BTC": FMT_EN,
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


def format_currency_simple(amount: float, currency_code: str, use_decimals: bool = True) -> str:
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

    fmt = CURRENCY_FORMATS.get(currency_code.upper(), FMT_NONE)

    # build number string
    raw = f"{abs(amount):.2f}" if use_decimals else f"{abs(amount):.0f}"
    int_part, dec_part = raw.split(".") if use_decimals else (raw, None)

    # apply thousands separator
    int_part = f"{int(int_part):,}".replace(",", fmt["thousands"])

    # combine integer + decimals
    formatted_amount = (
        f"{int_part}{fmt['decimal']}{dec_part}"
        if use_decimals else
        int_part
    )

    # handle symbol + fallback formatting
    if symbol == currency_code:
        return f"{symbol} {formatted_amount}"
    else:
        return f"{symbol}{formatted_amount}"
