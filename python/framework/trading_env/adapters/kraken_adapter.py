"""
FiniexTestingIDE - Kraken Broker Adapter
JSON-based implementation for Kraken crypto exchange

ARCHITECTURE:
- __init__: Reads JSON config 
- get_symbol_specification(): From JSON 
- get_broker_specification(): From JSON 
- Order methods: Feature gated 

This allows backtesting without Kraken API access.
"""

from typing import Dict, Any, List, Optional

from python.framework.types.broker_types import BrokerSpecification, BrokerType, MarginMode, SwapMode, SymbolSpecification
from python.framework.types.market_data_types import TickData
from .abstract_adapter import AbstractAdapter
from python.framework.types.order_types import (
    OrderCapabilities,
    MarketOrder,
    LimitOrder,
    StopLimitOrder,
    IcebergOrder,
    OrderDirection,
)


class KrakenAdapter(AbstractAdapter):
    """
    Kraken Crypto Exchange Adapter - JSON-based implementation.

    Backtesting: Works without KRAKEN_ENABLED (reads from JSON)
    Live Trading: Requires KRAKEN_ENABLED = True

    Kraken-Specific Features:
    - Maker/Taker fee structure (from fee_structure in config)
    - No swap fees (spot trading)
    - Leverage = 1 for pure spot (no margin)
    - Currencies explicit in config (base_currency, quote_currency)
    """

    def __init__(self, broker_config: Dict[str, Any]):
        """
        Initialize Kraken adapter with broker configuration.

        Args:
            broker_config: Kraken config loaded from JSON
        """
        super().__init__(broker_config)

        # Cache fee structure
        self._maker_fee = self._get_config_value(
            'fee_structure.maker_fee', 0.16)
        self._taker_fee = self._get_config_value(
            'fee_structure.taker_fee', 0.26)

    # ============================================
    # Configuration
    # ============================================

    def _validate_config(self) -> None:
        """
        Validate Kraken-specific configuration.

        Called after _validate_common_config() in base class.
        """
        # Kraken requires fee_structure with maker_taker model
        fee_structure = self.broker_config.get('fee_structure')
        if not fee_structure:
            raise ValueError(
                "❌ Kraken config requires 'fee_structure' section"
            )

        fee_model = fee_structure.get('model')
        if fee_model != 'maker_taker':
            raise ValueError(
                f"❌ Kraken requires fee_structure.model='maker_taker', got '{fee_model}'"
            )

        # Validate maker/taker fees exist
        if 'maker_fee' not in fee_structure or 'taker_fee' not in fee_structure:
            raise ValueError(
                "❌ Kraken fee_structure requires 'maker_fee' and 'taker_fee'"
            )

    def get_broker_name(self) -> str:
        """Get broker company name."""
        return self._broker_name

    def get_broker_type(self) -> BrokerType:
        """Get broker type identifier."""
        return BrokerType.KRAKEN_SPOT

    # ============================================
    # Capability Queries
    # ============================================

    def get_order_capabilities(self) -> OrderCapabilities:
        """
        Get Kraken order capabilities.

        Kraken supports:
        - Common: Market, Limit
        - Extended: StopLimit, Iceberg
        - NOT supported: Pure Stop orders (Kraken uses StopLimit)
        """
        return OrderCapabilities(
            market_orders=True,
            limit_orders=True,
            stop_orders=False,  # Kraken uses StopLimit instead
            stop_limit_orders=True,
            trailing_stop=False,
            iceberg_orders=True,
            hedging_allowed=self._hedging_allowed,
            partial_fills_supported=True
        )

    # ============================================
    # Order Creation (FEATURE GATED)
    # ============================================

    def create_market_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> MarketOrder:
        """
        Create Kraken market order.
        """

        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid market order: {error}")

        return MarketOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            max_slippage=kwargs.get('max_slippage'),
            comment=kwargs.get('comment', ''),
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
        Create Kraken limit order.
        """

        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid limit order: {error}")

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
        Create Kraken stop-limit order.
        """

        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid stop-limit order: {error}")

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
        Create Kraken iceberg order.
        """

        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid iceberg order: {error}")

        if visible_lots > lots:
            raise ValueError(
                f"Visible lots ({visible_lots}) cannot exceed total lots ({lots})"
            )

        return IcebergOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            visible_lots=visible_lots,
            price=price,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            comment=kwargs.get('comment', ''),
        )

    # ============================================
    # Order Validation (JSON-based)
    # ============================================

    def validate_order(
        self,
        symbol: str,
        lots: float
    ) -> tuple[bool, Optional[str]]:
        """
        Validate order parameters against Kraken limits.

        Args:
            symbol: Trading symbol
            lots: Order size

        Returns:
            (is_valid, error_message)
        """
        # Check symbol exists
        if symbol not in self.broker_config['symbols']:
            available = list(self.broker_config['symbols'].keys())
            raise ValueError(
                f"Symbol '{symbol}' not found. Available: {available}"
            )

        symbol_info = self.broker_config['symbols'][symbol]

        # Check trading allowed
        if not symbol_info.get('trade_allowed', True):
            return False, f"Trading not allowed for {symbol}"

        # Use common lot size validation from base class
        return self._validate_lot_size(symbol, lots)

    # ============================================
    # Symbol Information (JSON-based)
    # ============================================

    def get_all_aviable_symbols(self) -> List[str]:
        """
        Return list of all configured symbols.

        Returns:
            List of symbol strings (e.g., ["BTCUSD", "ETHUSD"])
        """
        return list(self.broker_config['symbols'].keys())

    def get_symbol_specification(self, symbol: str) -> SymbolSpecification:
        """
        Get fully typed symbol specification from JSON config.

        Args:
            symbol: Trading symbol (e.g., "BTCUSD")

        Returns:
            SymbolSpecification with all static properties
        """
        if symbol not in self.broker_config['symbols']:
            available = list(self.broker_config['symbols'].keys())
            raise ValueError(
                f"Symbol '{symbol}' not found in Kraken config.\n"
                f"Available symbols: {available}"
            )

        raw = self.broker_config['symbols'][symbol]

        # Kraken has explicit currencies in config
        base_currency = raw.get('base_currency', symbol[:3])
        quote_currency = raw.get('quote_currency', symbol[3:])
        # Crypto spot: margin in quote currency (USD usually)
        margin_currency = quote_currency

        return SymbolSpecification(
            # Identity
            symbol=symbol,
            description=raw.get('description', ''),

            # Trading Limits
            volume_min=raw.get('volume_min', 0.0001),
            volume_max=raw.get('volume_max', 10000.0),
            volume_step=raw.get('volume_step', 0.00000001),
            volume_limit=raw.get('volume_limit', 0.0),

            # Price Properties
            tick_size=raw.get('tick_size', 0.01),
            digits=raw.get('digits', 2),
            contract_size=raw.get('contract_size', 1),

            # Currency Information (explicit in Kraken config)
            base_currency=base_currency,
            quote_currency=quote_currency,
            margin_currency=margin_currency,

            # Trading Permissions
            trade_allowed=raw.get('trade_allowed', True),

            # Swap Configuration (none for crypto spot)
            swap_mode=SwapMode.NONE,
            swap_long=0.0,
            swap_short=0.0,
            swap_rollover3days=0,

            # Order Restrictions
            stops_level=raw.get('stops_level', 0),
            freeze_level=raw.get('freeze_level', 0)
        )

    def get_broker_specification(self) -> BrokerSpecification:
        """
        Get fully typed broker specification from JSON config.

        Returns:
            BrokerSpecification with all static broker properties
        """
        broker_info = self.broker_config.get('broker_info', {})
        trading_permissions = self.broker_config.get('trading_permissions', {})

        # Determine margin mode based on leverage
        leverage = broker_info.get('leverage', 1)
        if leverage == 1:
            margin_mode = MarginMode.NONE
        else:
            margin_mode_str = broker_info.get('margin_mode', 'retail_netting')
            try:
                margin_mode = MarginMode(margin_mode_str.lower())
            except ValueError:
                margin_mode = MarginMode.RETAIL_NETTING

        return BrokerSpecification(
            # Broker Identity
            company=broker_info.get('company', 'Kraken'),
            server=broker_info.get('server', 'kraken_spot'),
            broker_type=self.get_broker_type(),

            # Account Type
            trade_mode=broker_info.get('trade_mode', 'demo'),

            # Leverage & Margin (spot = no margin)
            leverage=leverage,
            margin_mode=margin_mode,
            margin_call_level=broker_info.get('margin_call_level', 0.0),
            stopout_level=broker_info.get('stopout_level', 0.0),
            stopout_mode=broker_info.get('stopout_mode', 'percent'),

            # Trading Permissions
            trade_allowed=trading_permissions.get('trade_allowed', True),
            expert_allowed=True,  # Always true for API trading
            hedging_allowed=broker_info.get('hedging_allowed', False),
            limit_orders=trading_permissions.get('limit_orders', 1000)
        )

# ============================================
# Fee Getters (Kraken-specific)
# ============================================

    def get_maker_fee(self) -> float:
        """
        Get maker fee percentage.

        Maker = adds liquidity (limit orders that don't immediately fill)

        Returns:
            Maker fee as percentage (e.g., 0.16 for 0.16%)
        """
        return self._maker_fee

    def get_taker_fee(self) -> float:
        """
        Get taker fee percentage.

        Taker = removes liquidity (market orders, limit orders that fill immediately)

        Returns:
            Taker fee as percentage (e.g., 0.26 for 0.26%)
        """
        return self._taker_fee
