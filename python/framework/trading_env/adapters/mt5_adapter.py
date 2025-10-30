"""
FiniexTestingIDE - MT5 Broker Adapter
Full implementation for MetaTrader 5 brokers

Loads existing broker_config.json from BrokerConfigExporter.mq5.
Supports MT5-specific order types: Market, Limit, Stop, StopLimit.
"""

from typing import Dict, Any, Optional
from .base_adapter import IOrderCapabilities
from python.framework.types.order_types import (
    OrderCapabilities,
    MarketOrder,
    LimitOrder,
    StopOrder,
    StopLimitOrder,
    OrderDirection,
)


class MT5Adapter(IOrderCapabilities):
    """
    MT5 Broker Adapter - Full implementation.

    Features:
    - Loads broker_config.json from BrokerConfigExporter.mq5
    - Supports Market, Limit, Stop, StopLimit orders
    - Validates lot sizes, margin requirements
    - Calculates commission from broker config

    Configuration Source:
    - mql5/sample_broker_data/broker_config_sample.json
    """

    def __init__(self, broker_config: Dict[str, Any]):
        """
        Initialize MT5 adapter with broker configuration.

        Args:
            broker_config: Loaded from broker_config.json
        """
        super().__init__(broker_config)

        # Cache frequently accessed values
        self._broker_name = self._get_config_value(
            'broker_info.company', 'Unknown MT5 Broker')
        self._leverage = self._get_config_value('broker_info.leverage', 100)
        self._hedging_allowed = self._get_config_value(
            'broker_info.hedging_allowed', False)

    # ============================================
    # Configuration
    # ============================================

    def _validate_config(self) -> None:
        """Validate MT5 broker configuration structure"""
        required_keys = ['broker_info', 'account_info', 'symbols']

        for key in required_keys:
            if key not in self.broker_config:
                raise ValueError(f"Missing required config key: {key}")

        # Validate broker_info structure
        broker_info = self.broker_config['broker_info']
        if 'company' not in broker_info:
            raise ValueError("Missing broker_info.company")

        # Validate at least one symbol exists
        if not self.broker_config['symbols']:
            raise ValueError("No symbols configured")

    def get_broker_name(self) -> str:
        """Get broker company name"""
        return self._broker_name

    def get_broker_type(self) -> str:
        """Get broker type identifier"""
        return "mt5_forex"  # MT5 Forex/CFD broker

    # ============================================
    # Capability Queries
    # ============================================

    def get_order_capabilities(self) -> OrderCapabilities:
        """
        Get MT5 order capabilities.

        MT5 supports:
        - Common: Market, Limit
        - Stop, StopLimit
        - No support for: TrailingStop (requires live connection), Iceberg
        """
        return OrderCapabilities(
            market_orders=True,
            limit_orders=True,
            stop_orders=True,
            stop_limit_orders=True,
            trailing_stop=False,  # Requires live MT5 connection
            iceberg_orders=False,  # Not supported by MT5
            hedging_allowed=self._hedging_allowed,
            partial_fills_supported=False  # MT5 fills orders atomically
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
        Create MT5 market order.

        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            direction: BUY or SELL
            lots: Order size (e.g., 0.1 = 10,000 units for Forex)
            **kwargs: stop_loss, take_profit, max_slippage, comment, magic_number

        Returns:
            MarketOrder ready for execution

        Raises:
            ValueError: If validation fails
        """
        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid market order: {error}")

        # Create order object
        return MarketOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            max_slippage=kwargs.get('max_slippage'),
            comment=kwargs.get('comment', ''),
            magic_number=kwargs.get('magic_number', 0)
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
        Create MT5 limit order.

        Args:
            symbol: Trading symbol
            direction: BUY or SELL
            lots: Order size
            price: Limit entry price
            **kwargs: stop_loss, take_profit, expiration, comment, magic_number

        Returns:
            LimitOrder ready for execution
        """
        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid limit order: {error}")

        # Validate price
        if price <= 0:
            raise ValueError(f"Invalid limit price: {price}")

        return LimitOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            price=price,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            expiration=kwargs.get('expiration'),
            comment=kwargs.get('comment', ''),
            magic_number=kwargs.get('magic_number', 0)
        )

    # ============================================
    # Extended Orders (Tier 2)
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
        Create MT5 stop order.

        Stop order becomes market order when stop_price is reached.
        """
        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid stop order: {error}")

        # Validate stop price
        if stop_price <= 0:
            raise ValueError(f"Invalid stop price: {stop_price}")

        return StopOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            stop_price=stop_price,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            comment=kwargs.get('comment', ''),
            magic_number=kwargs.get('magic_number', 0)
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
        Create MT5 stop-limit order.

        Order becomes limit order when stop_price is reached.
        """
        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid stop-limit order: {error}")

        # Validate prices
        if stop_price <= 0:
            raise ValueError(f"Invalid stop price: {stop_price}")
        if limit_price <= 0:
            raise ValueError(f"Invalid limit price: {limit_price}")

        return StopLimitOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            stop_price=stop_price,
            limit_price=limit_price,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            comment=kwargs.get('comment', ''),
            magic_number=kwargs.get('magic_number', 0)
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
        Validate MT5 order parameters.

        Checks:
        - Symbol exists and is tradeable
        - Lot size within broker limits
        - Lot step compliance

        Returns:
            (is_valid, error_message)
        """
        # Check symbol exists
        if symbol not in self.broker_config['symbols']:
            return False, f"Symbol {symbol} not found in broker configuration"

        symbol_info = self.broker_config['symbols'][symbol]

        # Check trading allowed
        if not symbol_info.get('trade_allowed', False):
            return False, f"Trading not allowed for {symbol}"

        # Use common lot size validation
        return self._validate_lot_size(symbol, lots)

    # ============================================
    # Symbol Information
    # ============================================

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get MT5 symbol specifications.

        Returns:
            Dict with volume_min, volume_max, volume_step, tick_size, etc.

        Raises:
            ValueError: If symbol not found
        """
        if symbol not in self.broker_config['symbols']:
            raise ValueError(
                f"Symbol {symbol} not found in broker configuration")

        return self.broker_config['symbols'][symbol]

    # ============================================
    # MT5-Specific Features
    # ============================================

    def get_leverage(self) -> int:
        """Get account leverage (e.g., 500 for 1:500)"""
        return self._leverage

    def calculate_margin_required(
        self,
        symbol: str,
        lots: float
    ) -> float:
        """
        Calculate required margin for order.

        Formula (Forex):
        Margin = (Lots * Contract_Size * Current_Price) / Leverage

        Args:
            symbol: Trading symbol
            lots: Order size

        Returns:
            Required margin in account currency
        """
        symbol_info = self.get_symbol_info(symbol)

        contract_size = symbol_info.get('contract_size', 100000)
        current_price = symbol_info.get('bid', 1.0)

        # Calculate margin
        margin = (lots * contract_size * current_price) / self._leverage

        return margin

    def get_spread_points(self, symbol: str) -> int:
        """Get current spread in points"""
        symbol_info = self.get_symbol_info(symbol)
        return symbol_info.get('spread_current', 0)

    def get_commission_per_lot(self, symbol: str) -> float:
        """
        Get commission per lot (if configured).

        Note: Commission not always exposed in broker_config.json.
        Returns 0.0 if not configured.
        """
        # Check if commission is in broker_info
        commission = self._get_config_value(
            'broker_info.commission_per_lot', 0.0)
        return commission

    def is_hedging_allowed(self) -> bool:
        """Check if broker allows hedging (opposite positions same symbol)"""
        return self._hedging_allowed
