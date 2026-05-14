"""
FiniexTestingIDE - Base Broker Adapter
Abstract interface for all broker adapters

All broker adapters (MT5, Kraken, etc.) must implement this interface.
Ensures consistent order creation API across different broker types.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, List, Optional
from python.framework.types.trading_env_types.broker_types import BrokerSpecification, BrokerType, FeeType, SymbolSpecification
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.live_types.live_execution_types import BrokerResponse
from python.framework.types.trading_env_types.order_types import (
    OrderCapabilities,
    OrderType,
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


class AbstractAdapter(ABC):
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
        self._validate_common_config()
        self._validate_config()

        self._leverage = self._get_config_value('broker_info.leverage', 1)
        # Cache frequently accessed values
        self._broker_name = self._get_config_value(
            'broker_info.company', 'unknown')
        self._hedging_allowed = self._get_config_value(
            'broker_info.hedging_allowed', False)

    # ============================================
    # Common Configuration Validation
    # ============================================

    def _validate_common_config(self) -> None:
        """
        Validate common configuration fields required by ALL brokers.

        Checks:
        - broker_info section exists with required fields
        - symbols section exists with at least one symbol
        - If leverage > 1: margin fields are mandatory

        Called before adapter-specific _validate_config().
        """
        # Check broker_info exists
        broker_info = self.broker_config.get('broker_info')
        if not broker_info:
            raise ValueError(
                "❌ Missing 'broker_info' section in broker config"
            )

        # Required broker_info fields (always)
        required_broker_fields = ['company',
                                  'server', 'trade_mode', 'leverage', 'company', 'hedging_allowed']
        for field in required_broker_fields:
            if field not in broker_info:
                raise ValueError(
                    f"❌ Missing required field 'broker_info.{field}' in broker config"
                )

        # Check symbols section
        symbols = self.broker_config.get('symbols')
        if not symbols:
            raise ValueError(
                "❌ Missing 'symbols' section in broker config"
            )
        if len(symbols) == 0:
            raise ValueError(
                "❌ No symbols configured in broker config"
            )

        # Leverage-dependent validation
        leverage = broker_info.get('leverage', 1)
        if leverage > 1:
            margin_fields = ['margin_mode',
                             'stopout_level', 'margin_call_level']
            missing = [f for f in margin_fields if f not in broker_info]
            if missing:
                raise ValueError(
                    f"❌ Broker has leverage={leverage} but missing margin fields: {missing}\n"
                    f"   When leverage > 1, these fields are mandatory in broker_info"
                )

        # Validate each symbol has required fields
        required_symbol_fields = [
            'volume_min', 'volume_max', 'volume_step',
            'contract_size', 'tick_size', 'digits', 'trade_allowed',
            'base_currency', 'quote_currency'
        ]
        for symbol_name, symbol_data in symbols.items():
            missing = [
                f for f in required_symbol_fields if f not in symbol_data]
            if missing:
                raise ValueError(
                    f"❌ Symbol '{symbol_name}' missing required fields: {missing}"
                )

            # Validate margin_currency if present (must be base or quote)
            margin_currency = symbol_data.get('margin_currency')
            if margin_currency:
                base = symbol_data['base_currency']
                quote = symbol_data['quote_currency']
                if margin_currency not in (base, quote):
                    raise ValueError(
                        f"❌ Symbol '{symbol_name}': margin_currency must be "
                        f"base_currency ('{base}') or quote_currency ('{quote}'), "
                        f"got '{margin_currency}'"
                    )

        # Validate fee_structure if present
        fee_structure = self.broker_config.get('fee_structure')
        if fee_structure:
            fee_model_str = fee_structure.get('model')
            if not fee_model_str:
                raise ValueError(
                    "❌ fee_structure present but missing 'model' field"
                )
            # Validate model is valid FeeType
            valid_models = [ft.value for ft in FeeType if ft in (
                FeeType.SPREAD, FeeType.MAKER_TAKER)]
            if fee_model_str not in valid_models:
                raise ValueError(
                    f"❌ Invalid fee_structure.model: '{fee_model_str}'\n"
                    f"   Valid values: {valid_models}"
                )

    # ============================================
    # Required: Configuration
    # ============================================

    @abstractmethod
    def _validate_config(self) -> None:
        """
        Validate broker-specific configuration.

        Called during __init__ AFTER _validate_common_config().
        Should raise ValueError if config invalid.
        """
        pass

    @abstractmethod
    def get_broker_name(self) -> str:
        """Get broker company name (e.g., 'IC Markets')"""
        pass

    @abstractmethod
    def get_broker_type(self) -> BrokerType:
        """Get broker type identifier (e.g., 'mt5', 'kraken_spot')"""
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
    # Optional: Live Order Execution (Tier 3)
    # ============================================

    def is_live_capable(self) -> bool:
        """
        Whether this adapter supports live order execution.

        Returns:
            False by default. Override to return True in live-capable adapters.
        """
        return False

    def on_tick(self, tick: TickData) -> None:
        """
        Receive a market tick update.

        OPTIONAL — Real adapters determine market state broker-side and
        typically ignore this. Mock/simulation adapters use it to keep
        their internal market view in sync with the tick loop so that
        execute_order() can fill at the current market price.

        Default: no-op.

        Args:
            tick: Current tick data
        """
        pass

    # ============================================
    # Tier 3 — Decoupled Operation Layers (Transport-Neutral)
    # ============================================
    #
    # Each Tier-3 operation (submit, query, cancel, modify) is split into
    # three pure layers that any live-capable adapter MUST provide:
    #
    #   _build_*_payload      Pure — assemble broker-specific request payload
    #   _do_request_*         Transport — send the request, return raw response
    #                         (HTTP, RPC, terminal bridge — implementation choice)
    #   _parse_*_response     Pure — convert raw response to BrokerResponse
    #
    # LiveRequestProcessor composes these layers — submit_open_order /
    # submit_close_order_async / modify_order_sync / cancel_order_sync /
    # query_order_sync drive them directly. There is no public
    # execute_order / check_order_status / cancel_order / modify_order
    # surface on the adapter.
    #
    # Default behavior: raise NotImplementedError. Live-capable adapters
    # override these. Adapters that only serve Tier 1+2 (backtesting) need
    # not implement them.
    # ============================================

    # --- Build payloads (pure) ---

    def _build_submit_payload(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        order_type: OrderType,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Build a broker-specific payload for an order submission request.

        Pure — no I/O, no state mutation.

        Args:
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Order size
            order_type: MARKET or LIMIT
            **kwargs: price (for LIMIT), stop_loss, take_profit, etc.

        Returns:
            Adapter-specific payload dict (passed to _do_request_submit)
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _build_submit_payload"
        )

    def _build_query_payload(self, broker_ref: str) -> Dict[str, Any]:
        """
        Build a broker-specific payload for an order status query.

        Pure — no I/O, no state mutation.

        Args:
            broker_ref: Broker's order reference ID

        Returns:
            Adapter-specific payload dict (passed to _do_request_query)
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _build_query_payload"
        )

    def _build_cancel_payload(self, broker_ref: str) -> Dict[str, Any]:
        """
        Build a broker-specific payload for an order cancellation request.

        Pure — no I/O, no state mutation.

        Args:
            broker_ref: Broker's order reference ID

        Returns:
            Adapter-specific payload dict (passed to _do_request_cancel)
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _build_cancel_payload"
        )

    def _build_modify_payload(
        self,
        broker_ref: str,
        symbol: str,
        new_price: Optional[float] = None,
        new_stop_loss: Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Build a broker-specific payload for an order modification request.

        Pure — no I/O, no state mutation.

        Args:
            broker_ref: Current broker order reference
            symbol: Trading symbol (required by some brokers)
            new_price: New limit price (None=no change)
            new_stop_loss: New stop loss (None=no change)
            new_take_profit: New take profit (None=no change)

        Returns:
            Adapter-specific payload dict (passed to _do_request_modify)
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _build_modify_payload"
        )

    # --- Transport (broker-side I/O, raises on error) ---

    def _do_request_submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send an order submission request to the broker. Raises on transport error.

        Transport-neutral: HTTP, RPC, terminal bridge — implementation choice.
        Designed to be called from a worker thread (post LiveRequestProcessor).

        Args:
            payload: Pre-built submission payload

        Returns:
            Raw broker response dict
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _do_request_submit"
        )

    def _do_request_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send an order status query to the broker. Raises on transport error.

        Args:
            payload: Pre-built query payload

        Returns:
            Raw broker response dict
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _do_request_query"
        )

    def _do_request_cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send an order cancellation request to the broker. Raises on transport error.

        Args:
            payload: Pre-built cancel payload

        Returns:
            Raw broker response dict
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _do_request_cancel"
        )

    def _do_request_modify(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send an order modification request to the broker. Raises on transport error.

        Args:
            payload: Pre-built modify payload

        Returns:
            Raw broker response dict
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _do_request_modify"
        )

    # --- Parse responses (pure) ---

    def _parse_submit_response(
        self,
        raw: Dict[str, Any],
        timestamp: datetime,
    ) -> BrokerResponse:
        """
        Convert a raw broker submit response into a BrokerResponse.

        Pure — no I/O, no state mutation.

        Args:
            raw: Raw broker response dict
            timestamp: Response receipt timestamp (UTC)

        Returns:
            BrokerResponse with broker_ref and status
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _parse_submit_response"
        )

    def _parse_query_response(
        self,
        raw: Dict[str, Any],
        broker_ref: str,
        timestamp: datetime,
    ) -> BrokerResponse:
        """
        Convert a raw broker query response into a BrokerResponse.

        Pure — no I/O, no state mutation.

        Args:
            raw: Raw broker response dict
            broker_ref: The broker reference that was queried
            timestamp: Response receipt timestamp (UTC)

        Returns:
            BrokerResponse with current status
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _parse_query_response"
        )

    def _parse_cancel_response(
        self,
        raw: Dict[str, Any],
        broker_ref: str,
        timestamp: datetime,
    ) -> BrokerResponse:
        """
        Convert a raw broker cancel response into a BrokerResponse.

        Pure — no I/O, no state mutation.

        Args:
            raw: Raw broker response dict
            broker_ref: The broker reference that was cancelled
            timestamp: Response receipt timestamp (UTC)

        Returns:
            BrokerResponse with cancellation status
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _parse_cancel_response"
        )

    def _parse_modify_response(
        self,
        raw: Dict[str, Any],
        original_broker_ref: str,
        timestamp: datetime,
    ) -> BrokerResponse:
        """
        Convert a raw broker modify response into a BrokerResponse.

        Pure — no I/O, no state mutation. Some brokers (e.g. Kraken EditOrder)
        return a NEW broker_ref that replaces the original; the caller is
        responsible for swapping the reference in any tracking index.

        Args:
            raw: Raw broker response dict
            original_broker_ref: The pre-modification broker reference
            timestamp: Response receipt timestamp (UTC)

        Returns:
            BrokerResponse with (potentially new) broker_ref
        """
        raise NotImplementedError(
            f"{self.get_broker_name()} does not implement _parse_modify_response"
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
    def get_all_aviable_symbols(self) -> List[str]:
        """
        Return a list of all symbol strings (e.g. ["EURUSD", "GBPUSD"]).
        """
        pass

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
        """
        pass

    @abstractmethod
    def get_broker_specification(self) -> BrokerSpecification:
        """
        Get fully typed broker specification.

        Returns static broker properties as typed dataclass.

        Returns:
            BrokerSpecification with all static broker properties
        """
        pass

    # ============================================
    # Required: Margin & Leverage
    # ============================================

    def get_leverage(self) -> int:
        """Get account leverage (e.g., 500 for 1:500)"""
        return self._leverage

    def calculate_margin_required(
        self,
        symbol: str,
        lots: float,
        tick: TickData,
        direction: OrderDirection
    ) -> float:
        """
        Calculate required margin for order.

        Formula depends on margin_currency:
        - If margin_currency == quote: margin = (lots * contract_size) / leverage
        - If margin_currency == base:  margin = (lots * contract_size * price) / leverage

        For spot (leverage=1): Returns full position value.
        For margin trading: Returns reduced margin requirement.

        Args:
            symbol: Trading symbol
            lots: Order size
            tick: Current tick data

        Returns:
            Required margin in account currency (quote currency)
        """
        symbol_spec = self.get_symbol_specification(symbol)
        contract_size = symbol_spec.contract_size
        leverage = self.get_leverage()

        # Check margin_currency to determine if price conversion needed
        if symbol_spec.margin_currency == symbol_spec.quote_currency:
            # Margin already in quote currency, no conversion needed
            position_value = lots * contract_size
        else:
            # Margin in base currency, convert to quote via price
            price = tick.ask if direction == OrderDirection.LONG else tick.bid
            position_value = lots * contract_size * price

        # Apply leverage (spot: leverage=1, returns full value)
        if leverage <= 1:
            return position_value
        return position_value / leverage

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
            # Handle floating point: remainder should be ~0 or ~lot_step
            if remainder > 1e-6 and (lot_step - remainder) > 1e-6:
                return False, f"Lot size {lots} not aligned with step {lot_step}"

        return True, None

    def _get_config_value(
        self,
        key_path: str,
        default: Any = None
    ) -> Any:
        """
        Get nested config value using dot notation.

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
