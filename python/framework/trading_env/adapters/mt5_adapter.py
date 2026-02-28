"""
FiniexTestingIDE - MT5 Broker Adapter
Full implementation for MetaTrader 5 brokers

Loads existing broker_config.json from BrokerConfigExporter.mq5.
Supports MT5-specific order types: Market, Limit, Stop, StopLimit.
"""

from typing import Dict, Any, List, Optional

from python.framework.types.market_data_types import TickData
from .abstract_adapter import AbstractAdapter
from python.framework.types.order_types import (
    OrderCapabilities,
    MarketOrder,
    LimitOrder,
    StopOrder,
    StopLimitOrder,
    OrderDirection,
)
from python.framework.types.broker_types import (
    BrokerType,
    SymbolSpecification,
    BrokerSpecification,
    SwapMode,
    MarginMode,
    extract_currencies_from_symbol
)


class MT5Adapter(AbstractAdapter):
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

    # ============================================
    # Configuration
    # ============================================

    def _validate_config(self) -> None:
        """
        Validate Mt5-specific configuration.

        Called after _validate_common_config() in base class.
        """
        pass

    def get_broker_name(self) -> str:
        """Get broker company name"""
        return self._broker_name

    def get_broker_type(self) -> BrokerType:
        """Get broker type identifier"""
        return BrokerType.MT5_FOREX  # MT5 Forex/CFD broker

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
            **kwargs: stop_loss, take_profit, max_slippage, comment

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
            comment=kwargs.get('comment', '')
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
            **kwargs: stop_loss, take_profit, expiration, comment

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
            comment=kwargs.get('comment', '')
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
            comment=kwargs.get('comment', '')
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
            comment=kwargs.get('comment', '')
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
            raise RuntimeError(
                "Symbol {symbol} not found in broker configuration")

        symbol_info = self.broker_config['symbols'][symbol]

        # Check trading allowed
        if not symbol_info.get('trade_allowed', False):
            raise RuntimeError(f"Trading not allowed for {symbol}")

        # Use common lot size validation
        return self._validate_lot_size(symbol, lots)

    # ============================================
    # Symbol Information
    # ============================================

    def get_all_aviable_symbols(self) -> List[str]:
        """
        Return a list of all symbol strings (e.g. ["EURUSD", "GBPUSD"]).
        """
        return list(self.broker_config["symbols"].keys())

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
        if symbol not in self.broker_config['symbols']:
            raise ValueError(
                f"Symbol {symbol} not found in broker configuration")

        raw = self.broker_config['symbols'][symbol]

        # Read currencies from config (unified: quote_currency for both MT5 and Kraken)
        base_currency = raw.get('base_currency')
        quote_currency = raw.get('quote_currency')
        margin_currency = raw.get('margin_currency', base_currency)

        if not base_currency or not quote_currency:
            raise ValueError(
                f"Symbol '{symbol}' missing required currency fields.\n"
                f"Required: 'base_currency', 'quote_currency'\n"
                f"Found: base={base_currency}, quote={quote_currency}"
            )

        # Parse swap mode
        swap_mode_str = raw.get('swap_mode', 'points').lower()
        try:
            swap_mode = SwapMode(swap_mode_str)
        except ValueError:
            swap_mode = SwapMode.POINTS  # Default fallback

        return SymbolSpecification(
            # Identity
            symbol=symbol,
            description=raw.get('description', ''),

            # Trading Limits
            volume_min=raw.get('volume_min', 0.01),
            volume_max=raw.get('volume_max', 100.0),
            volume_step=raw.get('volume_step', 0.01),
            volume_limit=raw.get('volume_limit', 0.0),

            # Price Properties
            tick_size=raw.get('tick_size', 0.00001),
            digits=raw.get('digits', 5),
            contract_size=raw.get('contract_size', 100000),

            # Currency Information
            base_currency=base_currency,
            quote_currency=quote_currency,
            margin_currency=margin_currency,

            # Trading Permissions
            trade_allowed=raw.get('trade_allowed', False),

            # Swap Configuration
            swap_mode=swap_mode,
            swap_long=raw.get('swap_long', 0.0),
            swap_short=raw.get('swap_short', 0.0),
            swap_rollover3days=raw.get('swap_rollover3days', 3),

            # Order Restrictions
            stops_level=raw.get('stops_level', 0),
            freeze_level=raw.get('freeze_level', 0)
        )

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
        broker_info = self.broker_config.get('broker_info', {})

        # Parse margin mode
        margin_mode_str = broker_info.get(
            'margin_mode', 'retail_hedging').lower()
        try:
            margin_mode = MarginMode(margin_mode_str)
        except ValueError:
            margin_mode = MarginMode.RETAIL_HEDGING  # Default fallback

        return BrokerSpecification(
            # Broker Identity
            company=broker_info.get('company', 'Unknown Broker'),
            server=broker_info.get('server', 'Unknown Server'),
            broker_type=self.get_broker_type(),

            # Account Type
            trade_mode=broker_info.get('trade_mode', 'demo'),

            # Leverage & Margin
            leverage=broker_info.get('leverage', 100),
            margin_mode=margin_mode,
            margin_call_level=broker_info.get('margin_call_level', 50.0),
            stopout_level=broker_info.get('stopout_level', 20.0),
            stopout_mode=broker_info.get('stopout_mode', 'percent'),

            # Trading Permissions
            trade_allowed=self.broker_config.get(
                'trading_permissions', {}).get('trade_allowed', True),
            expert_allowed=self.broker_config.get(
                'trading_permissions', {}).get('expert_allowed', True),
            hedging_allowed=broker_info.get('hedging_allowed', False),
            limit_orders=self.broker_config.get(
                'trading_permissions', {}).get('limit_orders', 0)
        )

    # ============================================
    # MT5-Specific Features
    # ============================================

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
