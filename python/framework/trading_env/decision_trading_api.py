"""
FiniexTestingIDE - Decision Trading API
Public interface for Decision Logic to interact with trading environment

This is the ONLY way Decision Logics should interact with the trade executor.
Framework code (BatchOrchestrator, Reporting) retains full executor access.

V1 Design:
- Market + Limit orders only
- Account info queries
- Position management
- Order-type validation BEFORE scenario start

Post-V1:
- Order history access
- EventBus integration

ARCHITECTURE NOTE:
- get_open_positions() returns ACTIVE positions (excluding those being closed)
- Latency simulation (pending orders) is hidden from Decision Logic
- This maintains clean separation: Decision Logic sees "logical state",
  the executor handles "execution details"

FUTURE NOTES:
- Tick→MS Migration: Currently delays are tick-based. Post-V1 will use millisecond-based
  timing with tick timestamp mapping for more realistic execution simulation.
- FiniexAutoTrader Integration: This API serves as the interface layer for both simulated
  and live trading. Decision Logics remain unchanged — only the executor is swapped:
  Backtesting: TradeSimulator, AutoTrader: LiveTradeExecutor.
  Example: DecisionTradingApi(LiveTradeExecutor(broker_config, ...), required_types)
"""

from datetime import datetime
from typing import Dict, List, Optional, Union

from .abstract_trade_executor import AbstractTradeExecutor
from .order_guard import OrderGuard
from .portfolio_manager import UNSET, _UnsetType
from python.framework.types.autotrader_types.autotrader_config_types import OrderGuardConfig
from python.framework.types.trading_env_types.broker_types import SymbolSpecification
from python.framework.types.trading_env_types.order_types import (
    OrderType,
    OrderDirection,
    OrderSide,
    OrderStatus,
    OrderResult,
    OrderCapabilities,
    ModificationResult,
    OpenOrderRequest,
    RejectionReason,
)

# Rejection reasons that indicate broker/account-side problems worth
# cooling down on. Local validation rejections (lot size, unsupported type)
# are decision bugs, not broker spam — they don't arm the cooldown.
_COOLDOWN_REJECTION_REASONS = frozenset({
    RejectionReason.INSUFFICIENT_MARGIN,
    RejectionReason.INSUFFICIENT_FUNDS,
    RejectionReason.BROKER_ERROR,
    RejectionReason.MARKET_CLOSED,
})
from .portfolio_manager import AccountInfo, Position


class DecisionTradingApi:
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
        required_order_types: List[OrderType],
        order_guard_config: Optional[OrderGuardConfig] = None,
    ):
        """
        Initialize Decision Trading API with order-type validation.

        Args:
            executor: AbstractTradeExecutor instance (TradeSimulator or LiveTradeExecutor)
            required_order_types: Order types that Decision Logic will use
            order_guard_config: Spam-protection guard configuration (defaults if None)

        Raises:
            ValueError: If any required order type is not supported by broker
        """
        self._executor = executor
        self._capabilities = executor.broker.get_order_capabilities()

        # CRITICAL: Validate order types BEFORE scenario starts!
        self._validate_order_types(required_order_types)

        # Spam-protection guard — rejection cooldown only. Business rules
        # (market type, balance, etc.) live in the executor.
        guard_cfg = order_guard_config or OrderGuardConfig()
        self._order_guard = OrderGuard(
            cooldown_seconds=guard_cfg.cooldown_seconds,
            max_consecutive_rejections=guard_cfg.max_consecutive_rejections,
        )

        # Register for async order outcomes (margin rejection at fill time, etc.)
        self._executor.set_order_outcome_callback(self._on_order_outcome)

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
        side: OrderSide,
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
        Decision logics speak OrderSide (BUY/SELL) — the executor resolves
        the side to an internal OrderDirection based on the trading model.
        Order-type validation already happened in __init__, so this will
        only fail due to runtime issues (insufficient margin, etc).

        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            order_type: MARKET, LIMIT, STOP, or STOP_LIMIT
            side: OrderSide.BUY or OrderSide.SELL (algo intent)
            lots: Position size
            price: Limit price (required for LIMIT and STOP_LIMIT, None for MARKET/STOP)
            stop_price: Stop trigger price (required for STOP and STOP_LIMIT, None for MARKET/LIMIT)
            stop_loss: Optional stop loss price level on resulting position
            take_profit: Optional take profit price level on resulting position
            comment: Order comment (e.g., strategy name)

        Returns:
            OrderResult with execution details
        """
        # Resolve algo-facing OrderSide to internal OrderDirection.
        # The executor owns this mapping — it knows the trading model.
        direction = self._executor.resolve_order_side(side)

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

        # Spam-protection guard — cooldown only. Time source is the
        # executor's current tick timestamp: simulated in backtests (keeps
        # cooldowns deterministic and sim-correct), wall-clock in live.
        now = self._executor.get_current_time()
        guard_result = self._order_guard.validate(request, now)
        if guard_result is not None:
            self._executor.record_guard_rejection(guard_result)
            return guard_result

        result = self._executor.open_order(request)

        # Sync rejections: update guard immediately for direct-return rejections
        # (lot validation, adapter exception, immediate broker rejection).
        # Only broker/account rejections feed the cooldown — local validation
        # rejections (invalid lot size, unsupported order type) are decision
        # bugs, not spam.
        # PENDING/SUBMITTED returns are NOT handled here — async outcomes
        # (margin check at fill time, broker polling) flow through the
        # _on_order_outcome callback registered in __init__.
        if result.is_rejected:
            if result.rejection_reason in _COOLDOWN_REJECTION_REASONS:
                self._order_guard.record_rejection(request.direction, now)

        return result

    # ============================================
    # Async Order Outcome Callback
    # ============================================

    def _on_order_outcome(
        self,
        direction: OrderDirection,
        result: OrderResult
    ) -> None:
        """
        Handle async order outcomes from the executor.

        Called by AbstractTradeExecutor._notify_outcome() when an order
        reaches a terminal state after the initial PENDING return —
        e.g. margin rejection at fill time, or successful fill after
        latency simulation.

        Args:
            direction: Order direction (LONG/SHORT)
            result: Terminal OrderResult (EXECUTED or REJECTED)
        """
        if result.is_rejected:
            if result.rejection_reason in _COOLDOWN_REJECTION_REASONS:
                self._order_guard.record_rejection(
                    direction,
                    self._executor.get_current_time(),
                )
        elif result.status == OrderStatus.EXECUTED:
            self._order_guard.record_success(direction)

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

    def get_symbol_spec(self, symbol: str) -> SymbolSpecification:
        """
        Get broker-side symbol specification (lot size, contract size,
        base/quote currencies, tick size, etc).

        Useful for algos that need to size orders against balances or derive
        base/quote currency names (e.g. for spot balance checks).

        Args:
            symbol: Trading symbol (e.g. 'BTCUSD')

        Returns:
            SymbolSpecification for the symbol
        """
        return self._executor.broker.get_symbol_specification(symbol)

    def is_spot_mode(self) -> bool:
        """
        Return True if the executor runs in spot mode (asset balances)
        rather than margin mode (free margin).
        """
        return self._executor.portfolio.is_spot_mode()

    def get_asset_balance(self, currency: str) -> float:
        """
        Get free balance of a specific currency (spot mode).

        Decision logics use this before submitting SELL orders on spot to
        verify sufficient base-currency balance is held. Margin strategies
        should use get_account_info().free_margin instead — asset balances
        are not the relevant quantity for margin trading.

        Args:
            currency: Asset symbol (e.g. 'BTC', 'USD', 'ETH')

        Returns:
            Available balance as float (0.0 if not held)
        """
        return self._executor.portfolio.get_asset_balance(currency)

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

    def get_current_time(self) -> datetime:
        """
        Canonical clock for decision logic timing (event timestamps, etc.).

        Returns tick timestamp: simulated time in backtests, wall-clock in live.

        Returns:
            Current tick timestamp (timezone-aware UTC)
        """
        return self._executor.get_current_time()

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

        Post-V1: Will provide full order history for analysis.
        V1: Not implemented.

        Args:
            symbol: Filter by symbol (None = all orders)

        Returns:
            List of OrderResult objects
        """
        raise NotImplementedError(
            "Order history is Post-V1 feature. "
            "Use get_open_positions() for V1."
        )
