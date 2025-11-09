"""
FiniexTestingIDE - Base Broker Adapter
Abstract interface for all broker adapters

All broker adapters (MT5, Kraken, etc.) must implement this interface.
Ensures consistent order creation API across different broker types.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from python.framework.types.broker_types import BrokerSpecification, SymbolSpecification
from python.framework.types.order_types import (
    OrderCapabilities,
    MarketOrder,
    LimitOrder,
    StopOrder,
    StopLimitOrder,
    IcebergOrder,
    OrderResult,
    OrderDirection,
    RejectionReason,
    create_rejection_result
)


class IOrderCapabilities(ABC):
    """
    Abstract interface for broker order capabilities.

    Every broker adapter must implement:
    1. Order creation methods (market, limit, extended)
    2. Capability query methods
    3. Broker-specific configuration loading

    Design Philosophy:
    - Common orders (market, limit) are REQUIRED
    - Extended orders (stop, iceberg) are OPTIONAL
    - Adapters declare their capabilities via get_order_capabilities()
    - DecisionLogic queries capabilities at runtime
    """

    def __init__(self, broker_config: Dict[str, Any]):
        """
        Initialize adapter with broker configuration.

        Args:
            broker_config: Broker-specific config (from JSON or API)
        """
        self.broker_config = broker_config
        self._validate_config()

    # ============================================
    # Required: Configuration
    # ============================================

    @abstractmethod
    def _validate_config(self) -> None:
        """
        Validate broker configuration.

        Called during __init__. Should raise ValueError if config invalid.
        """
        pass

    @abstractmethod
    def get_broker_name(self) -> str:
        """Get broker company name (e.g., 'IC Markets')"""
        pass

    @abstractmethod
    def get_broker_type(self) -> str:
        """Get broker type identifier (e.g., 'mt5_forex', 'kraken_spot')"""
        pass

    # ============================================
    # Required: Capability Queries
    # ============================================

    @abstractmethod
    def get_order_capabilities(self) -> OrderCapabilities:
        """
        Get broker order capabilities.

        Returns OrderCapabilities object with all supported features.
        Used by DecisionLogic to check what orders are possible.
        """
        pass

    # ============================================
    # Required: Common Orders (Tier 1)
    # ============================================

    @abstractmethod
    def create_market_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> MarketOrder:
        """
        Create market order (execute immediately at current price).

        ALL brokers MUST implement this.

        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            direction: BUY or SELL
            lots: Order size
            **kwargs: Optional params (stop_loss, take_profit, comment)

        Returns:
            MarketOrder object ready for execution

        Raises:
            ValueError: If parameters invalid
        """
        pass

    @abstractmethod
    def create_limit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        price: float,
        **kwargs
    ) -> LimitOrder:
        """
        Create limit order (execute at specified price or better).

        ALL brokers MUST implement this.

        Args:
            symbol: Trading symbol
            direction: BUY or SELL
            lots: Order size
            price: Limit price
            **kwargs: Optional params (stop_loss, take_profit, expiration)

        Returns:
            LimitOrder object ready for execution
        """
        pass

    # ============================================
    # Optional: Extended Orders (Tier 2)
    # ============================================

    def create_stop_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        stop_price: float,
        **kwargs
    ) -> StopOrder:
        """
        Create stop order (becomes market order when price reached).

        OPTIONAL - Not all brokers support this.
        Default implementation raises NotImplementedError.

        Override in subclass if broker supports stop orders.
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not support stop orders"
        )

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
        Create stop-limit order.

        OPTIONAL - Not all brokers support this.
        Default implementation raises NotImplementedError.
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not support stop-limit orders"
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
        Create iceberg order (large order split into chunks).

        OPTIONAL - Primarily crypto exchanges (Kraken, Binance).
        Default implementation raises NotImplementedError.
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not support iceberg orders"
        )

    # ============================================
    # Required: Order Validation
    # ============================================

    @abstractmethod
    def validate_order(
        self,
        symbol: str,
        lots: float
    ) -> tuple[bool, Optional[str]]:
        """
        Validate order parameters against broker limits.

        Checks:
        - Symbol is tradeable
        - Lot size within min/max bounds
        - Lot step compliance

        Args:
            symbol: Trading symbol
            lots: Order size

        Returns:
            (is_valid, error_message)
        """
        pass

    # ============================================
    # Required: Symbol Information
    # ============================================

    @abstractmethod
    def get_symbol_specification(self, symbol: str) -> SymbolSpecification:
        """
        Get fully typed symbol specification.

        Returns static symbol properties as typed dataclass.
        Does NOT include dynamic market data (tick_value, bid/ask).

        Args:
            symbol: Trading symbol (e.g., "GBPUSD")

        Returns:
            SymbolSpecification with all static properties

        Raises:
            ValueError: If symbol not found

        Example:
            spec = adapter.get_symbol_specification("GBPUSD")
            print(f"Min lot: {spec.volume_min}")
            print(f"Quote currency: {spec.quote_currency}")
        """
        pass

    @abstractmethod
    def get_broker_specification(self) -> BrokerSpecification:
        """
        Get fully typed broker specification.

        Returns static broker properties as typed dataclass.

        Returns:
            BrokerSpecification with all static broker properties

        Example:
            spec = adapter.get_broker_specification()
            print(f"Leverage: 1:{spec.leverage}")
            print(f"Margin call: {spec.margin_call_level}%")
        """
        pass

    # ============================================
    # Helper: Common Validation Logic
    # ============================================

    def _validate_lot_size(
        self,
        symbol: str,
        lots: float
    ) -> tuple[bool, Optional[str]]:
        """
        Common lot size validation logic.

        Can be reused by all adapters.
        """
        try:
            symbol_info = self.get_symbol_specification(symbol)
        except ValueError as e:
            raise e

        min_lot = symbol_info.volume_min
        max_lot = symbol_info.volume_max
        lot_step = symbol_info.volume_step

        # Check bounds
        if lots < min_lot:
            return False, f"Lot size {lots} below minimum {min_lot}"

        if lots > max_lot:
            return False, f"Lot size {lots} exceeds maximum {max_lot}"

        # Check step compliance
        if lot_step > 0:
            remainder = (lots - min_lot) % lot_step
            if abs(remainder) > 1e-8:  # Floating point tolerance
                return False, f"Lot size {lots} not aligned with step {lot_step}"

        return True, None

    def _get_config_value(
        self,
        key_path: str,
        default: Any = None
    ) -> Any:
        """
        Get nested config value using dot notation.

        Example:
            _get_config_value('symbols.EURUSD.volume_min', 0.01)

        Args:
            key_path: Dot-separated path (e.g., 'broker_info.leverage')
            default: Default value if key not found

        Returns:
            Config value or default
        """
        keys = key_path.split('.')
        value = self.broker_config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value
