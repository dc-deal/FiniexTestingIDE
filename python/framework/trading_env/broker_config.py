"""
FiniexTestingIDE - Broker Configuration
Unified interface for all broker types (MT5, Kraken, etc.)

Loads broker-specific JSON configs and delegates to appropriate adapter.
Provides common interface for TradeSimulator regardless of broker type.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional
from enum import Enum

from .adapters.base_adapter import IOrderCapabilities
from .adapters.mt5_adapter import MT5Adapter
from .adapters.kraken_adapter import KrakenAdapter, KRAKEN_ENABLED
from ..types.order_types import (
    OrderCapabilities,
    MarketOrder,
    LimitOrder,
    StopOrder,
    StopLimitOrder,
    IcebergOrder,
    OrderDirection,
)


class BrokerType(Enum):
    """Supported broker types"""
    MT5_FOREX = "mt5_forex"
    MT5_CFD = "mt5_cfd"
    MT5_CRYPTO = "mt5_crypto"
    KRAKEN_SPOT = "kraken_spot"
    BINANCE_FUTURES = "binance_futures"  # Post-MVP


class BrokerConfig:
    """
    Unified broker configuration interface.

    Responsibilities:
    - Load broker JSON from ./configs/brokers/
    - Instantiate correct adapter (MT5, Kraken, etc.)
    - Provide unified interface for TradeSimulator
    - Delegate broker-specific operations to adapter

    Usage:
        config = BrokerConfig.from_json("./configs/brokers/mt5/ic_markets_demo.json")

        # Unified interface
        leverage = config.get_max_leverage("EURUSD")
        margin = config.calculate_margin("EURUSD", 0.1)

        # Create orders
        order = config.create_market_order("EURUSD", OrderDirection.BUY, 0.1)
    """

    def __init__(self, broker_type: BrokerType, adapter: IOrderCapabilities):
        """
        Initialize broker config with adapter.

        Args:
            broker_type: Type of broker (MT5_FOREX, KRAKEN_SPOT, etc.)
            adapter: Instantiated broker adapter
        """
        self.broker_type = broker_type
        self.adapter = adapter

        # Cache adapter properties
        self._broker_name = adapter.get_broker_name()
        self._capabilities = adapter.get_order_capabilities()

    # ============================================
    # Factory Methods
    # ============================================

    @classmethod
    def from_json(cls, config_path: str) -> 'BrokerConfig':
        """
        Load broker config from JSON file.

        Automatically detects broker type and instantiates correct adapter.

        Args:
            config_path: Path to broker JSON config
                        (e.g., "config/brokers/mt5/ic_markets_demo.json")

        Returns:
            BrokerConfig instance with correct adapter

        Raises:
            FileNotFoundError: If config file not found
            ValueError: If broker type unknown or unsupported
        """
        path = Path(config_path)

        if not path.exists():
            raise FileNotFoundError(f"Broker config not found: {config_path}")

        # Load JSON
        with open(path, 'r') as f:
            raw_config = json.load(f)

        # Detect broker type
        broker_type = cls._detect_broker_type(raw_config, path)

        # Instantiate adapter
        adapter = cls._create_adapter(broker_type, raw_config)

        return cls(broker_type, adapter)

    @staticmethod
    def _detect_broker_type(config: Dict[str, Any], path: Path) -> BrokerType:
        """
        Detect broker type from config structure or file path.

        Detection logic:
        1. Check explicit 'broker_type' field in config
        2. Check file path (config/brokers/mt5/*, config/brokers/kraken/*)
        3. Check config structure (presence of MT5-specific fields)

        Args:
            config: Loaded broker config dict
            path: Config file path

        Returns:
            Detected BrokerType

        Raises:
            ValueError: If broker type cannot be determined
        """
        # Method 1: Explicit broker_type field
        if 'broker_type' in config:
            type_str = config['broker_type'].lower()
            try:
                return BrokerType(type_str)
            except ValueError:
                raise ValueError(f"Unknown broker type in config: {type_str}")

        # Method 2: File path detection
        path_str = str(path).lower()
        if '/mt5/' in path_str or '\\mt5\\' in path_str:
            return BrokerType.MT5_FOREX
        if '/kraken/' in path_str or '\\kraken\\' in path_str:
            return BrokerType.KRAKEN_SPOT

        # Method 3: Config structure detection (MT5-specific fields)
        if 'broker_info' in config and 'account_info' in config:
            # Check for MT5-specific fields
            broker_info = config.get('broker_info', {})
            if 'leverage' in broker_info and 'margin_mode' in broker_info:
                return BrokerType.MT5_FOREX

        raise ValueError(
            f"Cannot detect broker type from config: {path}. "
            "Add 'broker_type' field or organize in broker-specific folder."
        )

    @staticmethod
    def _create_adapter(
        broker_type: BrokerType,
        config: Dict[str, Any]
    ) -> IOrderCapabilities:
        """
        Create appropriate adapter for broker type.

        Args:
            broker_type: Detected broker type
            config: Raw broker configuration

        Returns:
            Instantiated adapter

        Raises:
            ValueError: If broker type not supported
        """
        if broker_type in [BrokerType.MT5_FOREX, BrokerType.MT5_CFD, BrokerType.MT5_CRYPTO]:
            return MT5Adapter(config)

        elif broker_type == BrokerType.KRAKEN_SPOT:
            if not KRAKEN_ENABLED:
                raise ValueError(
                    "Kraken adapter is feature-gated (KRAKEN_ENABLED=False). "
                    "Enable in Post-MVP phase."
                )
            return KrakenAdapter(config)

        else:
            raise ValueError(f"Broker type not supported: {broker_type}")

    # ============================================
    # Broker Information
    # ============================================

    def get_broker_name(self) -> str:
        """Get broker company name (e.g., 'IC Markets')"""
        return self._broker_name

    def get_broker_type_str(self) -> str:
        """Get broker type as string (e.g., 'mt5_forex')"""
        return self.broker_type.value

    def get_order_capabilities(self) -> OrderCapabilities:
        """Get broker order capabilities"""
        return self._capabilities

    # ============================================
    # Symbol Information
    # ============================================

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get symbol specifications.

        Args:
            symbol: Trading symbol

        Returns:
            Dict with min/max lot, tick size, spread, etc.
        """
        return self.adapter.get_symbol_info(symbol)

    def get_min_lot_size(self, symbol: str) -> float:
        """Get minimum lot size for symbol"""
        info = self.get_symbol_info(symbol)
        return info.get('volume_min', 0.01)

    def get_max_lot_size(self, symbol: str) -> float:
        """Get maximum lot size for symbol"""
        info = self.get_symbol_info(symbol)
        return info.get('volume_max', 100.0)

    def get_lot_step(self, symbol: str) -> float:
        """Get lot step increment for symbol"""
        info = self.get_symbol_info(symbol)
        return info.get('volume_step', 0.01)

    def get_tick_size(self, symbol: str) -> float:
        """Get minimum price movement for symbol"""
        info = self.get_symbol_info(symbol)
        return info.get('tick_size', 0.00001)

    def is_symbol_tradeable(self, symbol: str) -> bool:
        """Check if symbol is currently tradeable"""
        try:
            info = self.get_symbol_info(symbol)
            return info.get('trade_allowed', False)
        except ValueError:
            return False

    # ============================================
    # Order Creation (Delegated to Adapter)
    # ============================================

    def create_market_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> MarketOrder:
        """
        Create market order.

        Delegates to adapter for broker-specific validation.
        """
        return self.adapter.create_market_order(symbol, direction, lots, **kwargs)

    def create_limit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        price: float,
        **kwargs
    ) -> LimitOrder:
        """Create limit order"""
        return self.adapter.create_limit_order(symbol, direction, lots, price, **kwargs)

    def create_stop_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        stop_price: float,
        **kwargs
    ) -> StopOrder:
        """
        Create stop order (if supported).

        Raises:
            NotImplementedError: If broker doesn't support stop orders
        """
        return self.adapter.create_stop_order(symbol, direction, lots, stop_price, **kwargs)

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
        Create stop-limit order (if supported).

        Raises:
            NotImplementedError: If broker doesn't support stop-limit
        """
        return self.adapter.create_stop_limit_order(
            symbol, direction, lots, stop_price, limit_price, **kwargs
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
        Create iceberg order (if supported).

        Raises:
            NotImplementedError: If broker doesn't support iceberg orders
        """
        return self.adapter.create_iceberg_order(
            symbol, direction, lots, visible_lots, price, **kwargs
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
        Validate order parameters.

        Returns:
            (is_valid, error_message)
        """
        return self.adapter.validate_order(symbol, lots)

    # ============================================
    # Broker-Specific Features (Optional)
    # ============================================

    def get_max_leverage(self, symbol: Optional[str] = None) -> int:
        """
        Get maximum leverage (if applicable).

        MT5: Account-level leverage (e.g., 500)
        Kraken: Symbol-specific leverage (e.g., 5 for crypto)

        Args:
            symbol: Optional symbol (some brokers have symbol-specific leverage)

        Returns:
            Maximum leverage multiplier
        """
        if hasattr(self.adapter, 'get_leverage'):
            return self.adapter.get_leverage()
        return 1  # Default: No leverage

    def calculate_margin(
        self,
        symbol: str,
        lots: float
    ) -> float:
        """
        Calculate required margin for order.

        Only available for brokers with margin trading (MT5, Kraken margin).

        Args:
            symbol: Trading symbol
            lots: Order size

        Returns:
            Required margin in account currency
        """
        if hasattr(self.adapter, 'calculate_margin_required'):
            return self.adapter.calculate_margin_required(symbol, lots)

        # Fallback: No margin calculation available
        return 0.0
