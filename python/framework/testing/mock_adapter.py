# ============================================
# python/framework/testing/mock_adapter.py
# ============================================
"""
FiniexTestingIDE - Mock Broker Adapter
Simulates broker responses for testing LiveTradeExecutor without a real broker.

Extends AbstractAdapter with mock data (BTCUSD from real Kraken config)
and configurable execution behavior (instant fill, delayed, reject, timeout).

Modes:
    instant_fill:  execute_order() returns FILLED immediately
    delayed_fill:  execute_order() returns PENDING, check_order_status() returns FILLED
    reject_all:    execute_order() returns REJECTED
    timeout:       execute_order() returns PENDING, check_order_status() stays PENDING

Usage:
    adapter = MockBrokerAdapter(mode="instant_fill")
    broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
    executor = LiveTradeExecutor(broker_config, ...)
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from python.framework.trading_env.adapters.abstract_adapter import AbstractAdapter
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import (
    BrokerSpecification,
    BrokerType,
    MarginMode,
    SwapMode,
    SymbolSpecification,
)
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus, BrokerResponse
from python.framework.types.trading_env_types.order_types import (
    OrderCapabilities,
    OrderDirection,
    OrderType,
    MarketOrder,
    LimitOrder,
)


class MockExecutionMode(Enum):
    """Configurable execution behavior for mock adapter."""
    INSTANT_FILL = "instant_fill"
    DELAYED_FILL = "delayed_fill"
    REJECT_ALL = "reject_all"
    TIMEOUT = "timeout"


# ============================================
# Minimal broker config for mock (based on real Kraken BTCUSD)
# ============================================
_MOCK_BROKER_CONFIG: Dict[str, Any] = {
    "broker_info": {
        "company": "MockBroker",
        "server": "mock_test",
        "name": "mock_test",
        "trade_mode": "demo",
        "leverage": 1,
        "hedging_allowed": False,
    },
    "fee_structure": {
        "model": "maker_taker",
        "maker_fee": 0.16,
        "taker_fee": 0.26,
        "fee_currency": "quote",
    },
    "symbols": {
        "BTCUSD": {
            "description": "BTC vs USD",
            "base_currency": "BTC",
            "quote_currency": "USD",
            "trade_allowed": True,
            "volume_min": 0.00005,
            "volume_max": 10000,
            "volume_step": 1e-8,
            "contract_size": 1,
            "tick_size": 0.1,
            "digits": 1,
            "stops_level": 0,
            "freeze_level": 0,
        },
        "ETHUSD": {
            "description": "ETH vs USD",
            "base_currency": "ETH",
            "quote_currency": "USD",
            "trade_allowed": True,
            "volume_min": 0.002,
            "volume_max": 10000,
            "volume_step": 1e-8,
            "contract_size": 1,
            "tick_size": 0.01,
            "digits": 2,
            "stops_level": 0,
            "freeze_level": 0,
        },
    },
}


class MockBrokerAdapter(AbstractAdapter):
    """
    Mock broker adapter for testing LiveTradeExecutor.

    Provides real symbol specifications (BTCUSD from Kraken config)
    with configurable execution behavior. No network calls.

    Supports additional symbols via add_symbol() for multi-symbol tests.
    """

    def __init__(
        self,
        mode: MockExecutionMode = MockExecutionMode.INSTANT_FILL,
        broker_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize mock adapter.

        Args:
            mode: Execution behavior (instant_fill, delayed_fill, reject_all, timeout)
            broker_config: Override config (default: BTCUSD mock config)
        """
        config = broker_config or _MOCK_BROKER_CONFIG.copy()
        # Deep copy symbols to avoid mutation
        if broker_config is None:
            import copy
            config = copy.deepcopy(_MOCK_BROKER_CONFIG)

        super().__init__(config)

        self._mode = mode
        self._order_counter = 0
        # Track pending orders for delayed_fill mode
        self._mock_pending: Dict[str, Dict[str, Any]] = {}
        # Configurable fill price offset (simulates slippage)
        self._slippage_points: float = 0.0
        # Last-seen tick per symbol (fed via on_tick) — used to fill
        # orders at the current market price instead of a static fallback.
        self._last_ticks: Dict[str, TickData] = {}

    # ============================================
    # Configuration (required by AbstractAdapter)
    # ============================================

    def _validate_config(self) -> None:
        """Mock config is always valid."""
        pass

    def get_broker_name(self) -> str:
        """Get mock broker name."""
        return self._broker_name

    def get_broker_type(self) -> BrokerType:
        """Mock uses Kraken spot type."""
        return BrokerType.KRAKEN_SPOT

    def get_order_capabilities(self) -> OrderCapabilities:
        """Mock supports market orders only (feature gating)."""
        return OrderCapabilities(
            market_orders=True,
            limit_orders=False,
            stop_orders=False,
            stop_limit_orders=False,
            trailing_stop=False,
            iceberg_orders=False,
            hedging_allowed=False,
            partial_fills_supported=False,
        )

    # ============================================
    # Order Creation (required by AbstractAdapter)
    # ============================================

    def create_market_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> MarketOrder:
        """
        Create market order object.

        Args:
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Order size
            **kwargs: Additional parameters

        Returns:
            MarketOrder object
        """
        return MarketOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            stop_loss=kwargs.get("stop_loss"),
            take_profit=kwargs.get("take_profit"),
            comment=kwargs.get("comment", ""),
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
        Create limit order (not supported in mock adapter).

        Args:
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Order size
            price: Limit price
            **kwargs: Additional parameters

        Returns:
            LimitOrder object
        """
        raise NotImplementedError(
            "MockBrokerAdapter does not support limit orders")

    # ============================================
    # Order Validation (required by AbstractAdapter)
    # ============================================

    def validate_order(
        self,
        symbol: str,
        lots: float,
    ) -> tuple[bool, Optional[str]]:
        """
        Validate order parameters against mock broker limits.

        Args:
            symbol: Trading symbol
            lots: Order size

        Returns:
            (is_valid, error_message)
        """
        if symbol not in self.broker_config["symbols"]:
            return False, f"Symbol {symbol} not available in mock broker"
        return self._validate_lot_size(symbol, lots)

    # ============================================
    # Symbol Information (required by AbstractAdapter)
    # ============================================

    def get_all_aviable_symbols(self) -> List[str]:
        """Return list of mock symbols."""
        return list(self.broker_config["symbols"].keys())

    def get_symbol_specification(self, symbol: str) -> SymbolSpecification:
        """
        Get symbol specification from mock config.

        Args:
            symbol: Trading symbol (e.g., "BTCUSD")

        Returns:
            SymbolSpecification with static properties
        """
        if symbol not in self.broker_config["symbols"]:
            available = list(self.broker_config["symbols"].keys())
            raise ValueError(
                f"Symbol '{symbol}' not found in mock config. "
                f"Available: {available}"
            )

        raw = self.broker_config["symbols"][symbol]
        base_currency = raw.get("base_currency", symbol[:3])
        quote_currency = raw.get("quote_currency", symbol[3:])

        return SymbolSpecification(
            symbol=symbol,
            description=raw.get("description", ""),
            volume_min=raw.get("volume_min", 0.0001),
            volume_max=raw.get("volume_max", 10000.0),
            volume_step=raw.get("volume_step", 1e-8),
            volume_limit=raw.get("volume_limit", 0.0),
            tick_size=raw.get("tick_size", 0.1),
            digits=raw.get("digits", 1),
            contract_size=raw.get("contract_size", 1),
            base_currency=base_currency,
            quote_currency=quote_currency,
            margin_currency=quote_currency,
            trade_allowed=raw.get("trade_allowed", True),
            swap_mode=SwapMode.NONE,
            swap_long=0.0,
            swap_short=0.0,
            swap_rollover3days=0,
            stops_level=raw.get("stops_level", 0),
            freeze_level=raw.get("freeze_level", 0),
        )

    def get_broker_specification(self) -> BrokerSpecification:
        """Get mock broker specification."""
        return BrokerSpecification(
            company=self._broker_name,
            server="mock_test",
            broker_type=BrokerType.KRAKEN_SPOT,
            trade_mode="demo",
            leverage=1,
            margin_mode=MarginMode.NONE,
            margin_call_level=0.0,
            stopout_level=0.0,
            stopout_mode="percent",
            trade_allowed=True,
            expert_allowed=True,
            hedging_allowed=False,
            limit_orders=0,
        )

    # ============================================
    # Live Execution (mock implementations)
    # ============================================

    def is_live_capable(self) -> bool:
        """Mock adapter supports live execution."""
        return True

    def on_tick(self, tick: TickData) -> None:
        """
        Store the latest tick so execute_order() can fill at the
        current market price (bid for SHORT, ask for LONG).
        """
        self._last_ticks[tick.symbol] = tick

    def _resolve_market_fill_price(
        self,
        symbol: str,
        direction: OrderDirection,
        expected_price: Optional[float],
    ) -> float:
        """
        Determine fill price for a market order.

        Priority:
        1. Explicit ``expected_price`` kwarg (test override).
        2. Last tick seen via on_tick — ask for LONG, bid for SHORT.
        3. Fallback constant (legacy tests without tick feed).

        Args:
            symbol: Trading symbol
            direction: LONG (BUY) or SHORT (SELL)
            expected_price: Optional explicit override

        Returns:
            Fill price in quote currency
        """
        if expected_price is not None:
            return expected_price
        last_tick = self._last_ticks.get(symbol)
        if last_tick is not None:
            return last_tick.ask if direction == OrderDirection.LONG else last_tick.bid
        # Legacy fallback — tests that never feed a tick still work.
        return 50000.0

    def execute_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        order_type: OrderType,
        **kwargs
    ) -> BrokerResponse:
        """
        Simulate order execution based on configured mode.

        Args:
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Order size
            order_type: Order type
            **kwargs: Additional parameters

        Returns:
            BrokerResponse with mode-dependent status
        """
        self._order_counter += 1
        broker_ref = f"MOCK-{self._order_counter:06d}"
        now = datetime.now(timezone.utc)

        if self._mode == MockExecutionMode.REJECT_ALL:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason="Mock broker: reject_all mode",
                timestamp=now,
            )

        market_price = self._resolve_market_fill_price(
            symbol, direction, kwargs.get("expected_price"))

        if self._mode == MockExecutionMode.INSTANT_FILL:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.FILLED,
                fill_price=market_price + self._slippage_points,
                filled_lots=lots,
                timestamp=now,
            )

        # DELAYED_FILL or TIMEOUT: return pending, track internally
        self._mock_pending[broker_ref] = {
            "symbol": symbol,
            "direction": direction,
            "lots": lots,
            "submitted_at": now,
            "expected_price": market_price,
        }

        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.PENDING,
            timestamp=now,
        )

    def check_order_status(self, broker_ref: str) -> BrokerResponse:
        """
        Check mock order status.

        In delayed_fill mode: returns FILLED.
        In timeout mode: always returns PENDING (never fills).

        Args:
            broker_ref: Broker's order reference ID

        Returns:
            BrokerResponse with current status
        """
        now = datetime.now(timezone.utc)

        if broker_ref not in self._mock_pending:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason="Unknown broker_ref",
                timestamp=now,
            )

        if self._mode == MockExecutionMode.TIMEOUT:
            # Never fills — stays pending forever
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.PENDING,
                timestamp=now,
            )

        # DELAYED_FILL: fill on first status check
        order_data = self._mock_pending.pop(broker_ref)
        fill_price = order_data["expected_price"] + self._slippage_points

        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.FILLED,
            fill_price=fill_price,
            filled_lots=order_data["lots"],
            timestamp=now,
        )

    def cancel_order(self, broker_ref: str) -> BrokerResponse:
        """
        Cancel a mock pending order.

        Args:
            broker_ref: Broker's order reference ID

        Returns:
            BrokerResponse with CANCELLED status
        """
        now = datetime.now(timezone.utc)
        self._mock_pending.pop(broker_ref, None)

        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.CANCELLED,
            timestamp=now,
        )

    def modify_order(
        self,
        broker_ref: str,
        symbol: str,
        new_price: Optional[float] = None,
        new_stop_loss: Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> BrokerResponse:
        """
        Simulate order modification based on configured mode.

        In REJECT_ALL mode: returns REJECTED.
        Otherwise: returns FILLED (modification accepted).
        Unknown broker_ref: returns REJECTED.

        Args:
            broker_ref: Broker's order reference ID
            symbol: Trading symbol (unused in mock, required for interface compliance)
            new_price: New limit price (None=no change)
            new_stop_loss: New stop loss level (None=no change)
            new_take_profit: New take profit level (None=no change)

        Returns:
            BrokerResponse with modification status
        """
        now = datetime.now(timezone.utc)

        if self._mode == MockExecutionMode.REJECT_ALL:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason="Mock broker: reject_all mode",
                timestamp=now,
            )

        # Check if order exists in pending (delayed/timeout) or was instant-filled
        if broker_ref not in self._mock_pending:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=f"Unknown broker_ref: {broker_ref}",
                timestamp=now,
            )

        # Apply modification to mock pending state
        order_data = self._mock_pending[broker_ref]
        if new_price is not None:
            order_data["expected_price"] = new_price
        # SL/TP stored but not used in mock fill logic
        if new_stop_loss is not None:
            order_data["stop_loss"] = new_stop_loss
        if new_take_profit is not None:
            order_data["take_profit"] = new_take_profit

        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.FILLED,
            timestamp=now,
        )

    # ============================================
    # Mock Configuration Helpers
    # ============================================

    def set_mode(self, mode: MockExecutionMode) -> None:
        """
        Change execution mode at runtime.

        Args:
            mode: New execution behavior
        """
        self._mode = mode

    def set_slippage(self, points: float) -> None:
        """
        Set fill price offset for slippage simulation.

        Args:
            points: Price offset applied to fills (positive = worse price)
        """
        self._slippage_points = points

    def add_symbol(self, symbol: str, spec: Dict[str, Any]) -> None:
        """
        Register an additional symbol specification at runtime.

        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            spec: Symbol spec dict with keys matching _MOCK_BROKER_CONFIG symbols entries
        """
        self.broker_config['symbols'][symbol] = spec

    def get_maker_fee(self) -> float:
        """Get mock maker fee percentage."""
        return self._get_config_value("fee_structure.maker_fee", 0.16)

    def get_taker_fee(self) -> float:
        """Get mock taker fee percentage."""
        return self._get_config_value("fee_structure.taker_fee", 0.26)
