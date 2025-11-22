"""
FiniexTestingIDE - Kraken Broker Adapter (DUMMY)
Placeholder implementation for Kraken crypto exchange

FEATURE GATED: KRAKEN_ENABLED = False (MVP)
Post-MVP: Full implementation with Kraken API integration
"""

from typing import Dict, Any, List, Optional

from python.framework.types.broker_types import SymbolSpecification
from .base_adapter import IOrderCapabilities
from python.framework.types.order_types import (
    OrderCapabilities,
    MarketOrder,
    LimitOrder,
    StopLimitOrder,
    IcebergOrder,
    OrderDirection,
)


# ============================================
# Feature Gate - Disable Kraken for MVP
# ============================================
KRAKEN_ENABLED = False  # Set to True when implementing Post-MVP


class KrakenAdapter(IOrderCapabilities):
    """
    Kraken Crypto Exchange Adapter - DUMMY IMPLEMENTATION.

    Feature Gate: KRAKEN_ENABLED = False

    Purpose:
    - Demonstrates adapter architecture
    - Reserves interface for Post-MVP expansion
    - Shows how non-MT5 brokers would be integrated

    Kraken-Specific Features (Post-MVP):
    - Maker/Taker fee structure
    - Funding rates for margin positions
    - Iceberg orders (large order splitting)
    - Stop-Limit orders (Kraken supports, MT5 supports)
    - No traditional stop orders (Kraken limitation)

    When Implementing Post-MVP:
    1. Set KRAKEN_ENABLED = True
    2. Implement Kraken API client
    3. Replace NotImplementedError with real logic
    4. Add Kraken-specific config schema
    """

    def __init__(self, broker_config: Dict[str, Any]):
        """
        Initialize Kraken adapter.

        Args:
            broker_config: Kraken-specific configuration

        Raises:
            RuntimeError: If KRAKEN_ENABLED = False
        """
        if not KRAKEN_ENABLED:
            raise RuntimeError(
                "Kraken adapter is feature-gated (KRAKEN_ENABLED=False). "
                "This is a dummy implementation for MVP. "
                "Enable in Post-MVP phase."
            )

        super().__init__(broker_config)

    # ============================================
    # Configuration
    # ============================================

    def _validate_config(self) -> None:
        """Validate Kraken broker configuration"""
        # Dummy implementation - would validate Kraken API keys, etc.
        required_keys = ['broker_type', 'api_credentials', 'symbols']

        for key in required_keys:
            if key not in self.broker_config:
                raise ValueError(f"Missing required Kraken config key: {key}")

    def get_broker_name(self) -> str:
        """Get broker name"""
        return "Kraken"

    def get_broker_type(self) -> str:
        """Get broker type identifier"""
        return "kraken_spot"  # Kraken spot market (not futures)

    # ============================================
    # Capability Queries
    # ============================================

    def get_order_capabilities(self) -> OrderCapabilities:
        """
        Get Kraken order capabilities.

        Kraken features:
        - Common: Market, Limit
        - StopLimit, Iceberg
        - NOT supported: Stop (Kraken uses StopLimit instead)
        """
        return OrderCapabilities(
            market_orders=True,
            limit_orders=True,
            stop_orders=False,  # Kraken doesn't have pure stop orders
            stop_limit_orders=True,  # Kraken's primary stop mechanism
            trailing_stop=False,  # Not supported
            iceberg_orders=True,  # Kraken-specific feature
            hedging_allowed=False,  # Kraken doesn't support hedging
            partial_fills_supported=True  # Large orders can partial fill
        )

    # ============================================
    # Common Orders (Tier 1)
    # ============================================

    def create_market_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> MarketOrder:
        """
        Create Kraken market order (DUMMY).

        Post-MVP: Implement Kraken API integration.
        """
        raise NotImplementedError(
            "Kraken market orders not implemented (feature gated). "
            "Enable KRAKEN_ENABLED for Post-MVP."
        )

    def create_limit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        price: float,
        **kwargs
    ) -> LimitOrder:
        """
        Create Kraken limit order (DUMMY).

        Post-MVP: Implement Kraken API integration.
        """
        raise NotImplementedError(
            "Kraken limit orders not implemented (feature gated). "
            "Enable KRAKEN_ENABLED for Post-MVP."
        )

    # ============================================
    # Extended Orders (Tier 2)
    # ============================================

    def create_stop_limit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        stop_price: float,
        limit_price: float,
        **kwargs
    ) -> StopLimitOrder:
        """
        Create Kraken stop-limit order (DUMMY).

        Kraken uses stop-limit as primary stop mechanism.
        No pure stop orders available.

        Post-MVP: Implement Kraken API integration.
        """
        raise NotImplementedError(
            "Kraken stop-limit orders not implemented (feature gated). "
            "Enable KRAKEN_ENABLED for Post-MVP."
        )

    def create_iceberg_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        visible_lots: float,
        price: float,
        **kwargs
    ) -> IcebergOrder:
        """
        Create Kraken iceberg order (DUMMY).

        Iceberg orders split large orders into smaller visible chunks.
        Kraken-specific feature not available in MT5.

        Post-MVP: Implement Kraken API integration.
        """
        raise NotImplementedError(
            "Kraken iceberg orders not implemented (feature gated). "
            "Enable KRAKEN_ENABLED for Post-MVP."
        )

    # ============================================
    # Order Validation
    # ============================================

    def validate_order(
        self,
        symbol: str,
        lots: float
    ) -> tuple[bool, Optional[str]]:
        """
        Validate Kraken order (DUMMY).

        Post-MVP: Implement Kraken symbol validation.
        """
        raise NotImplementedError(
            "Kraken order validation not implemented (feature gated)."
        )

    # ============================================
    # Symbol Information
    # ============================================

    def get_all_aviable_symbols(self) -> List[str]:
        """
        Return a list of all symbol strings (e.g. ["EURUSD", "GBPUSD"]).
        """
        raise NotImplementedError(
            "Kraken symbol list info not implemented (feature gated)."
        )

    def get_symbol_specification(self, symbol: str) -> SymbolSpecification:
        """
        Get Kraken symbol specs (DUMMY).

        Post-MVP: Query Kraken API for symbol information.
        """
        raise NotImplementedError(
            "Kraken symbol info not implemented (feature gated)."
        )

    # ============================================
    # Kraken-Specific Features (Post-MVP)
    # ============================================

    def get_maker_fee(self) -> float:
        """
        Get maker fee percentage.

        Kraken charges different fees for maker vs taker orders.
        Maker = adds liquidity (limit orders)
        """
        raise NotImplementedError("Kraken maker fees - Post-MVP")

    def get_taker_fee(self) -> float:
        """
        Get taker fee percentage.

        Taker = removes liquidity (market orders)
        """
        raise NotImplementedError("Kraken taker fees - Post-MVP")

    def get_funding_rate(self, symbol: str) -> float:
        """
        Get funding rate for margin positions.

        Kraken charges funding rates for leveraged positions.
        Different from Forex swap rates.
        """
        raise NotImplementedError("Kraken funding rates - Post-MVP")


# ============================================
# Helper: Create Dummy Config for Testing
# ============================================

def create_kraken_dummy_config() -> Dict[str, Any]:
    """
    Create dummy Kraken config for testing adapter architecture.

    This allows testing the adapter interface without full implementation.

    Returns:
        Minimal Kraken config structure
    """
    return {
        "broker_type": "kraken_spot",
        "api_credentials": {
            "api_key": "dummy_key",
            "api_secret": "dummy_secret"
        },
        "symbols": {
            "BTCUSD": {
                "volume_min": 0.001,
                "volume_max": 100.0,
                "volume_step": 0.001,
                "tick_size": 0.01,
                "maker_fee": 0.0016,
                "taker_fee": 0.0026,
                "trade_allowed": True
            }
        }
    }
