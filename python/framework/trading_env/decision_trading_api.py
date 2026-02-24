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

from typing import Dict, List, Optional, Union

from .abstract_trade_executor import AbstractTradeExecutor
from .portfolio_manager import UNSET, _UnsetType
from ..types.order_types import (
    OrderType,
    OrderDirection,
    OrderResult,
    OrderCapabilities,
    ModificationResult,
    OpenOrderRequest,
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
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "",
    ) -> OrderResult:
        """
        Send order to trading environment.

        This is the main entry point for Decision Logics to execute trades.
        Order-type validation already happened in __init__, so this will
        only fail due to runtime issues (insufficient margin, etc).

        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            order_type: MARKET, LIMIT, STOP, or STOP_LIMIT
            direction: OrderDirection.LONG or OrderDirection.SHORT
            lots: Position size
            price: Limit price (required for LIMIT and STOP_LIMIT, None for MARKET/STOP)
            stop_price: Stop trigger price (required for STOP and STOP_LIMIT, None for MARKET/LIMIT)
            stop_loss: Optional stop loss price level on resulting position
            take_profit: Optional take profit price level on resulting position
            comment: Order comment (e.g., strategy name)

        Returns:
            OrderResult with execution details
        """
        request = OpenOrderRequest(
            symbol=symbol,
            order_type=order_type,
            direction=direction,
            lots=lots,
            price=price,
            stop_price=stop_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            comment=comment,
        )
        return self._executor.open_order(request)

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

        Includes ALL pending worlds: latency pipeline, active limit orders,
        active stop orders. Used by single-position strategies that need
        to know about any outstanding order activity.

        Returns:
            True if any orders are pending (open or close)

        Example:
            if self.trading_api.has_pending_orders():
                return  # Wait for pending orders to resolve
        """
        return self._executor.has_pending_orders()

    def has_pipeline_orders(self) -> bool:
        """
        Are there orders in the latency/submission pipeline only?

        Unlike has_pending_orders(), this excludes broker-accepted orders
        waiting for price trigger (active limit/stop orders). Use this
        when you need to know if orders are still "in transit" to the broker.

        Returns:
            True if orders are in the latency pipeline (not yet broker-accepted)
        """
        return self._executor.has_pipeline_orders()

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
    # Position Modification
    # ============================================

    def modify_position(
        self,
        position_id: str,
        stop_loss: Union[float, None, _UnsetType] = UNSET,
        take_profit: Union[float, None, _UnsetType] = UNSET
    ) -> ModificationResult:
        """
        Modify position stop loss and/or take profit levels.

        Args:
            position_id: Position to modify
            stop_loss: New SL price, None to remove, UNSET to keep current
            take_profit: New TP price, None to remove, UNSET to keep current

        Returns:
            ModificationResult with success status and rejection reason
        """
        return self._executor.modify_position(
            position_id=position_id,
            new_stop_loss=stop_loss,
            new_take_profit=take_profit
        )

    def modify_limit_order(
        self,
        order_id: str,
        price: Union[float, _UnsetType] = UNSET,
        stop_loss: Union[float, None, _UnsetType] = UNSET,
        take_profit: Union[float, None, _UnsetType] = UNSET
    ) -> ModificationResult:
        """
        Modify a pending limit order's price, SL, and/or TP.

        Only applies to active limit orders (post-latency, waiting for price trigger).

        Args:
            order_id: Pending limit order ID
            price: New limit price (UNSET=keep current)
            stop_loss: New SL price, None to remove, UNSET to keep current
            take_profit: New TP price, None to remove, UNSET to keep current

        Returns:
            ModificationResult with success status and rejection reason
        """
        return self._executor.modify_limit_order(
            order_id=order_id,
            new_price=price,
            new_stop_loss=stop_loss,
            new_take_profit=take_profit
        )

    def modify_stop_order(
        self,
        order_id: str,
        stop_price: Union[float, _UnsetType] = UNSET,
        price: Union[float, _UnsetType] = UNSET,
        stop_loss: Union[float, None, _UnsetType] = UNSET,
        take_profit: Union[float, None, _UnsetType] = UNSET
    ) -> ModificationResult:
        """
        Modify a pending stop order's trigger price, limit price, SL, and/or TP.

        Only applies to active stop orders (post-latency, waiting for trigger price).

        Args:
            order_id: Pending stop order ID
            stop_price: New trigger price (UNSET=keep current)
            price: New limit price for STOP_LIMIT (UNSET=keep current)
            stop_loss: New SL price, None to remove, UNSET to keep current
            take_profit: New TP price, None to remove, UNSET to keep current

        Returns:
            ModificationResult with success status and rejection reason
        """
        return self._executor.modify_stop_order(
            order_id=order_id,
            new_stop_price=stop_price,
            new_limit_price=price,
            new_stop_loss=stop_loss,
            new_take_profit=take_profit
        )

    def cancel_limit_order(self, order_id: str) -> bool:
        """
        Cancel an active limit order by order ID.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if order was found and cancelled
        """
        return self._executor.cancel_limit_order(order_id)

    def cancel_stop_order(self, order_id: str) -> bool:
        """
        Cancel an active stop order by order ID.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if order was found and cancelled
        """
        return self._executor.cancel_stop_order(order_id)

    def get_active_order_counts(self) -> Dict[str, int]:
        """
        Get counts of active orders by world (latency, limit, stop).

        Returns:
            Dict with keys "latency_queue", "active_limits", "active_stops"
        """
        return self._executor.get_active_order_counts()

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
