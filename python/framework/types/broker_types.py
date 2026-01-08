"""
FiniexTestingIDE - Broker Type Definitions
Fully typed dataclasses for broker and symbol specifications

These types represent STATIC broker configuration (unchanging properties).
Dynamic market data (bid/ask, tick_value) is NOT included here.

Architecture:
- SymbolSpecification: Static symbol properties (lot sizes, tick size, currencies)
- BrokerSpecification: Static broker properties (leverage, margin levels, company info)
- Separation of concerns: Static config vs. dynamic market data
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum


class SwapMode(Enum):
    """Swap calculation mode for overnight positions"""
    POINTS = "points"              # Swap in points (MT5)
    INTEREST_CURRENT = "interest_current"  # Current interest rate
    INTEREST_OPEN = "interest_open"        # Interest at position open
    PERCENTAGE = "percentage"      # Percentage of position value


class MarginMode(Enum):
    """Account margin calculation mode"""
    RETAIL_NETTING = "retail_netting"    # Single position per symbol
    RETAIL_HEDGING = "retail_hedging"    # Multiple positions allowed
    EXCHANGE = "exchange"                # Exchange margin calculation


@dataclass(frozen=True)
class SymbolSpecification:
    """
    Static symbol specifications (unchanging properties).

    These properties are loaded from broker config and represent
    the trading rules and characteristics of a symbol.

    DOES NOT include dynamic market data:
    - NO tick_value (currency conversion rate - changes with market)
    - NO bid/ask prices (real-time market data)
    - NO spread_current (real-time spread)

    Use Cases:
    - Order validation (min/max lots, tick size)
    - Margin calculation (contract size, leverage)
    - Price formatting (digits)
    - Currency extraction (base/quote for tick_value calculation)
    """

    # Identity
    symbol: str
    description: str

    # Trading Limits
    volume_min: float        # Minimum lot size (e.g., 0.01)
    volume_max: float        # Maximum lot size (e.g., 100.0)
    volume_step: float       # Lot increment (e.g., 0.01)
    volume_limit: float      # Max total volume (0.0 = no limit)

    # Price Properties
    # Minimum price movement (e.g., 0.00001 for EURUSD)
    tick_size: float
    digits: int              # Decimal places (e.g., 5 for EURUSD)
    contract_size: int       # Units per 1.0 lot (e.g., 100,000 for Forex)

    # Currency Information (extracted from symbol or config)
    base_currency: str       # GBPUSD → "GBP"
    quote_currency: str      # GBPUSD → "USD" (profit currency)
    margin_currency: str     # Currency for margin calculation

    # Trading Permissions
    trade_allowed: bool      # Can this symbol be traded?

    # Swap Configuration (for overnight positions)
    swap_mode: SwapMode
    swap_long: float         # Swap rate for long positions (points or %)
    swap_short: float        # Swap rate for short positions (points or %)
    # Day of week for triple swap (0=Sunday, 3=Wednesday)
    swap_rollover3days: int

    # Order Restrictions (MT5-specific)
    # Minimum distance for SL/TP in points (0 = no restriction)
    stops_level: int
    freeze_level: int        # Freeze distance in points (0 = no restriction)


@dataclass(frozen=True)
class BrokerSpecification:
    """
    Static broker specifications (unchanging properties).

    Represents broker-level configuration like leverage, margin requirements,
    and trading permissions.

    Use Cases:
    - Margin calculation (leverage)
    - Risk management (margin call/stopout levels)
    - Order validation (order type support, hedging rules)
    """

    # Broker Identity
    company: str             # Broker name (e.g., "IC Markets")
    server: str              # Server name (e.g., "ICMarkets-Demo")
    broker_type: str         # Broker type identifier (e.g., "mt5_forex")

    # Account Type
    trade_mode: str          # "demo" or "real"

    # Leverage & Margin
    leverage: int            # Account leverage (e.g., 500 for 1:500)
    margin_mode: MarginMode  # Margin calculation mode
    margin_call_level: float  # Margin call threshold (e.g., 50.0 = 50%)
    stopout_level: float     # Stop out threshold (e.g., 20.0 = 20%)
    stopout_mode: str        # "percent" or "money"

    # Trading Permissions
    trade_allowed: bool      # Can trade on this account?
    expert_allowed: bool     # Can run expert advisors (EAs)?
    hedging_allowed: bool    # Can open opposite positions on same symbol?
    limit_orders: int        # Max number of pending orders (0 = unlimited)


# ============================================
# Helper Functions
# ============================================

def extract_currencies_from_symbol(symbol: str) -> tuple[str, str, str]:
    """
    Extract base, quote, and margin currency from symbol name.

    Forex convention: BASEQUOTE (6 characters)
    - GBPUSD → base=GBP, quote=USD, margin=GBP
    - EURUSD → base=EUR, quote=USD, margin=EUR
    - USDJPY → base=USD, quote=JPY, margin=USD

    Args:
        symbol: Trading symbol (e.g., "GBPUSD")

    Returns:
        (base_currency, quote_currency, margin_currency)

    Raises:
        ValueError: If symbol format invalid
    """
    if len(symbol) != 6:
        raise ValueError(
            f"Cannot extract currencies from symbol '{symbol}': "
            f"Expected 6 characters (e.g., GBPUSD, EURUSD)"
        )

    base = symbol[:3].upper()
    quote = symbol[3:6].upper()
    margin = base  # Forex convention: margin in base currency

    return base, quote, margin
