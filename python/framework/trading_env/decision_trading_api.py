"""
FiniexTestingIDE - Decision Trading API
Public interface for Decision Logic to interact with trading environment

This is the ONLY way Decision Logics should interact with the trade executor.
Framework code (BatchOrchestrator, Reporting) retains full executor access.

MVP Design:
- Market + Limit orders only
- Account info queries
- Position management
- Order-type validation BEFORE scenario start

Post-MVP:
- Position modification (SL/TP changes)
- Order history access
- EventBus integration

ARCHITECTURE NOTE:
- get_open_positions() returns ACTIVE positions (excluding those being closed)
- Latency simulation (pending orders) is hidden from Decision Logic
- This maintains clean separation: Decision Logic sees "logical state",
  the executor handles "execution details"

FUTURE NOTES:
- Tick→MS Migration: Currently delays are tick-based. Post-MVP will use millisecond-based
  timing with tick timestamp mapping for more realistic execution simulation.
- FiniexAutoTrader Integration: This API serves as the interface layer for both simulated
  and live trading. When integrating FiniexAutoTrader, Decision Logics remain unchanged.
  The executor is swapped via config: executor_mode = "simulation" | "live_dry_run"
  Example: DecisionTradingAPI(LiveTradeExecutor(broker_config, ...), required_types)
"""

from typing import List, Optional

from .abstract_trade_executor import AbstractTradeExecutor
from ..types.order_types import (
    OrderType,
    OrderDirection,
    OrderResult,
    OrderCapabilities,
)
from .portfolio_manager import AccountInfo, Position


class DecisionTradingAPI:
    """
    Public API for Decision Logic trading operations.

    This class acts as a gatekeeper between Decision Logic and the trade executor,
    providing only safe, validated operations.

    Key Features:
    - Order-type validation at creation time (BEFORE scenario runs)
    - Clean public API (only what Decision Logics need)
    - Framework retains full executor access
    - Executor-agnostic: works with TradeSimulator and LiveTradeExecutor
    """

    def __init__(
        self,
        executor: AbstractTradeExecutor,
        required_order_types: List[OrderType]
    ):
        """
        Initialize Decision Trading API with order-type validation.

        Args:
            executor: AbstractTradeExecutor instance (TradeSimulator or LiveTradeExecutor)
            required_order_types: Order types that Decision Logic will use

        Raises:
            ValueError: If any required order type is not supported by broker
        """
        self._executor = executor
        self._capabilities = executor.broker.get_order_capabilities()

        # CRITICAL: Validate order types BEFORE scenario starts!
        self._validate_order_types(required_order_types)

    # ============================================
    # Order Type Validation
    # ============================================

    def _validate_order_types(self, required_types: List[OrderType]):
        """
        Validate that broker supports all required order types.

        This is the core safety mechanism - prevents runtime failures
        by catching unsupported order types at scenario creation time.

        Args:
            required_types: List of OrderType that Decision Logic needs

        Raises:
            ValueError: If any order type not supported by broker

        Example:
            # Decision Logic requires Market + Limit
            required = [OrderType.MARKET, OrderType.LIMIT]

            # But broker only supports Market
            # → ValueError raised BEFORE scenario starts
            # → Clear error message to user
            # → No wasted computation on invalid scenario
        """
        unsupported = []

        for order_type in required_types:
            if not self._capabilities.supports_order_type(order_type):
                unsupported.append(order_type)

        if unsupported:
            supported_types = self._get_supported_order_types()
            raise ValueError(
                f"❌ Broker '{self._executor.broker.adapter.get_broker_name()}' "
                f"does not support required order types!\n"
                f"Required: {[t.value for t in required_types]}\n"
                f"Unsupported: {[t.value for t in unsupported]}\n"
                f"Broker supports: {[t.value for t in supported_types]}\n"
                f"→ Change Decision Logic or use different broker config!"
            )

    def _get_supported_order_types(self) -> List[OrderType]:
        """Get list of order types supported by broker"""
        supported = []

        for order_type in OrderType:
            if self._capabilities.supports_order_type(order_type):
                supported.append(order_type)

        return supported
    # ============================================
    # Public API: Order Execution
    # ============================================

    def send_order(
        self,
        symbol: str,
        order_type: OrderType,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> OrderResult:
        """
        Send order to trading environment.

        This is the main entry point for Decision Logics to execute trades.
        Order-type validation already happened in __init__, so this will
        only fail due to runtime issues (insufficient margin, etc).

        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            order_type: OrderType.MARKET or OrderType.LIMIT (MVP)
            direction: OrderDirection.LONG or OrderDirection.SHORT
            lots: Position size
            **kwargs: Optional params (stop_loss, take_profit, price for limit)

        Returns:
            OrderResult with execution details

        Example:
            result = self.trading_api.send_order(
                symbol="EURUSD",
                order_type=OrderType.MARKET,
                direction=OrderDirection.LONG,
                lots=0.1,
                stop_loss=1.0950,
                take_profit=1.1050
            )
        """
        return self._executor.open_order(
            symbol=symbol,
            order_type=order_type,
            direction=direction,
            lots=lots,
            **kwargs
        )

    # ============================================
    # Public API: Account Queries
    # ============================================

    def get_account_info(self, order_direction: OrderDirection) -> AccountInfo:
        """
        Get current account information.

        Returns account state including:
        - balance: Total account balance
        - equity: Balance + unrealized P&L
        - margin_used: Margin locked in open positions
        - free_margin: Available margin for new trades
        - margin_level: (equity / margin_used) * 100

        Returns:
            AccountInfo dataclass with all account metrics
        """
        return self._executor.get_account_info(order_direction)

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """
        Get list of open positions.

        Args:
            symbol: Filter by symbol (None = all positions)

        Returns:
            List of Position objects with details:
            - position_id: Unique identifier
            - symbol: Trading symbol
            - direction: BUY or SELL
            - lots: Position size
            - open_price: Entry price
            - current_price: Current market price
            - unrealized_pnl: Floating profit/loss
            - stop_loss: SL level (if set)
            - take_profit: TP level (if set)

        Example:
            positions = self.trading_api.get_open_positions("EURUSD")
            for pos in positions:
                if pos.unrealized_pnl < -100:
                    self.close_position(pos.position_id)
        """
        all_positions = self._executor.get_open_positions()

        if symbol:
            return [p for p in all_positions if p.symbol == symbol]

        return all_positions

    def get_position(self, position_id: str) -> Optional[Position]:
        """
        Get specific position by ID.

        Args:
            position_id: Position identifier

        Returns:
            Position object or None if not found
        """
        return self._executor.get_position(position_id)

    # ============================================
    # Public API: Pending Order Awareness
    # ============================================

    def has_pending_orders(self) -> bool:
        """
        Are there any orders in flight (submitted but not yet filled)?

        Used by single-position strategies to avoid double-ordering
        during execution delays.

        Returns:
            True if any orders are pending (open or close)

        Example:
            if self.trading_api.has_pending_orders():
                return  # Wait for pending orders to resolve
        """
        return self._executor.has_pending_orders()

    def is_pending_close(self, position_id: str) -> bool:
        """
        Is this specific position currently being closed?

        Used by multi-position strategies to avoid duplicate close
        submissions for the same position.

        Args:
            position_id: Position to check

        Returns:
            True if a close order is in flight for this position

        Example:
            for pos in positions:
                if not self.trading_api.is_pending_close(pos.position_id):
                    self.trading_api.close_position(pos.position_id)
        """
        return self._executor.is_pending_close(position_id)

    def close_position(
        self,
        position_id: str,
        lots: Optional[float] = None
    ) -> OrderResult:
        """
        Close position (full or partial).

        Args:
            position_id: Position to close
            lots: Lots to close (None = close all)

        Returns:
            OrderResult with close execution details
        """
        return self._executor.close_position(position_id, lots)
    # ============================================
    # Public API: Broker Capabilities
    # ============================================

    def get_order_capabilities(self) -> OrderCapabilities:
        """
        Get broker order capabilities.

        Useful for Decision Logics that want to check capabilities
        at runtime (though validation already happened in __init__).

        Returns:
            OrderCapabilities object with supported features

        Example:
            caps = self.trading_api.get_order_capabilities()
            if caps.trailing_stop:
                # Use trailing stop
                pass
        """
        return self._capabilities

    # ============================================
    # Utility Methods
    # ============================================

    def get_broker_name(self) -> str:
        """Get name of connected broker"""
        return self._executor.broker.adapter.get_broker_name()

    def get_leverage(self, symbol: Optional[str] = None) -> float:
        """
        Get leverage for symbol.

        Args:
            symbol: Trading symbol (None = default leverage)

        Returns:
            Leverage multiplier (e.g., 100.0 for 1:100)
        """
        if symbol:
            return self._executor.broker.get_symbol_leverage(symbol)
        return self._executor.broker.get_max_leverage()

    # ============================================
    # Post-MVP Features (Feature-Gated)
    # ============================================

    def modify_position(
        self,
        position_id: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> bool:
        """
        Modify position stop loss and take profit.

        Post-MVP: Will allow dynamic SL/TP management.
        MVP: Not implemented.

        Args:
            position_id: Position to modify
            stop_loss: New SL level (None = no change)
            take_profit: New TP level (None = no change)

        Returns:
            True if modification successful
        """
        raise NotImplementedError(
            "Position modification is Post-MVP feature. "
            "Use send_order() with stop_loss/take_profit for MVP."
        )

    def get_order_history(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """
        Get historical orders (executed + rejected).

        Post-MVP: Will provide full order history for analysis.
        MVP: Not implemented.

        Args:
            symbol: Filter by symbol (None = all orders)

        Returns:
            List of OrderResult objects
        """
        raise NotImplementedError(
            "Order history is Post-MVP feature. "
            "Use get_open_positions() for MVP."
        )
