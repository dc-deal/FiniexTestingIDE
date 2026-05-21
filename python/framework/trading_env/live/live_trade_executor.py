# ============================================
# python/framework/trading_env/live/live_trade_executor.py
# ============================================
"""
FiniexTestingIDE - Live Trade Executor
Live broker execution via adapter API (Horizon 2).

Inherits from AbstractTradeExecutor — provides live order execution
with broker adapter communication and LiveRequestProcessor for pending
order management.

Architecture:
    FiniexAutoTrader (live runner, replaces process_tick_loop)
        |
        +-- Live Data Feed (WebSocket / REST poll) -> delivers ticks
        |
        +-- LiveTradeExecutor
            +-- on_tick(tick)                -> inherited: prices + _process_pending_orders()
            +-- _process_pending_orders()    -> polls broker for fills, handles timeouts
            +-- open_order()                 -> sends to broker via adapter
            +-- close_position()             -> sends close to broker
            +-- _fill_open_order()           -> INHERITED: portfolio update
            +-- _fill_close_order()          -> INHERITED: portfolio update

Fill processing is INHERITED from AbstractTradeExecutor — no duplication needed.
The base class handles: portfolio updates, fee calculations, statistics, PnL.

Feature gating: MARKET + LIMIT orders supported. Limit order modification is broker-side (future).
"""

import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Union

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor, ExecutorMode
from python.framework.trading_env.portfolio_manager import UNSET, _UnsetType
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.types.trading_env_types.latency_simulator_types import (
    ModificationRequest,
    PendingOperation,
    PendingOrder,
    PendingOrderAction,
    PendingOrderOutcome,
)
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason, EntryType
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
    TimeoutConfig,
)
from python.framework.types.live_types.live_request_types import QueryResponse, TradesQueryResponse
from python.framework.types.trading_env_types.order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    OrderResult,
    FillType,
    RejectionReason,
    ModificationRejectionReason,
    ModificationResult,
    ModificationStatus,
    OpenOrderRequest,
    create_rejection_result,
)
from python.framework.types.trading_env_types.pending_order_stats_types import PendingOrderStats


class LiveTradeExecutor(AbstractTradeExecutor):
    """
    Live Trade Executor — broker execution via adapter API.

    Extends AbstractTradeExecutor with live-specific behavior:
    - Order submission via LiveRequestProcessor (Tier-3 layered adapter)
    - Pending order tracking via LiveRequestProcessor
    - Broker status polling in _process_pending_orders()
    - Timeout detection for unresponsive orders

    The AbstractTradeExecutor base provides:
    - Portfolio management (positions, balance, margin)
    - Fill processing (_fill_open_order, _fill_close_order)
    - Fee calculations, statistics, order history
    - Price tracking, broker info queries

    This subclass implements:
    - HOW orders are submitted (broker API)
    - HOW fills are detected (broker polling)
    - HOW pending state is tracked (LiveRequestProcessor)
    """

    def __init__(
        self,
        broker_config: BrokerConfig,
        initial_balance: float,
        account_currency: str,
        logger: AbstractLogger,
        timeout_config: Optional[TimeoutConfig] = None,
        order_history_max: int = 10000,
        trade_history_max: int = 5000,
        spot_mode: bool = False,
        initial_balances: Optional[dict[str, float]] = None,
        poll_interval_ms: int = 5000,
    ):
        """
        Initialize live trade executor.

        Args:
            broker_config: Broker configuration (must have live-capable adapter)
            initial_balance: Starting account balance
            account_currency: Account currency
            logger: Logger instance
            timeout_config: Timeout thresholds for order monitoring
            order_history_max: Max order history entries (0=unlimited)
            trade_history_max: Max trade history entries (0=unlimited)
            spot_mode: Enable spot trading mode
            initial_balances: Asset inventory for spot mode
            poll_interval_ms: Minimum wall-clock interval between consecutive
                status polls for the same active LIMIT order. Throttle
                for #320's async polling scheduler. Default 5000 ms.
        """
        super().__init__(
            broker_config=broker_config,
            initial_balance=initial_balance,
            account_currency=account_currency,
            logger=logger,
            order_history_max=order_history_max,
            trade_history_max=trade_history_max,
            spot_mode=spot_mode,
            initial_balances=initial_balances,
        )

        # Validate adapter supports live execution
        if not broker_config.adapter.is_live_capable():
            raise ValueError(
                f"Adapter '{broker_config.get_broker_name()}' is not live-capable. "
                f"Live execution requires adapter.is_live_capable() == True."
            )

        self._timeout_config = timeout_config or TimeoutConfig()
        self._poll_interval_ms = poll_interval_ms

        # Live order tracker with broker ref tracking
        self._request_processor = LiveRequestProcessor(
            logger=logger,
            timeout_config=self._timeout_config,
        )

        # Live mode: broker handles SL/TP server-side
        self._executor_mode = ExecutorMode.LIVE

        # #318 — Tracker for in-flight position SL/TP modifications.
        # Active only when adapter declares native_position_sl_tp=True (MT5 in
        # #209). For Kraken-style adapters the modify_position path falls back
        # to instant portfolio.modify_position and this tracker stays empty.
        self._pending_position_modifications: Dict[str, ModificationRequest] = {}

        # #327 — Multi-consumer fan-out for TradesQueryResponse. Consumers
        # (DriftAuditor, future Reconciliation #151) register via
        # add_trades_response_consumer(). The executor's own
        # _handle_trades_response runs first; consumers receive a copy of the
        # response regardless of success/failure or executor's resolution path.
        self._trades_response_consumers: List[Callable[[TradesQueryResponse], None]] = []

        self.logger.info(
            f"LiveTradeExecutor initialized with broker: "
            f"{broker_config.get_broker_name()} "
            f"(timeout={self._timeout_config.order_timeout_seconds}s)"
        )

        # Wire the processor's drain-inbox hooks into the executor.
        # All hooks run on the main thread inside drain_inbox().
        # _fill_open_order / _fill_close_order already append the
        # EXECUTED result to order_history and notify listeners, so
        # they can be used directly as hooks — no wrapper needed.
        # limit_response routes LIMIT submit responses back here so we
        # can update _active_limit_orders (Hybrid pattern — shared storage).
        # #318 — modify/cancel/position-modify responses route to handlers
        # that mutate _active_*_orders / portfolio (Hybrid pattern).
        self._request_processor.set_executor_hooks(
            fill_open=self._fill_open_order,
            fill_close=self._fill_close_order,
            on_rejection=self._record_async_rejection,
            limit_response=self._handle_limit_submit_response,
            modify_response=self._handle_modify_response,
            cancel_response=self._handle_cancel_response,
            position_modify_response=self._handle_position_modify_response,
            trades_response=self._handle_trades_response,
            query_response=self._handle_query_response,
        )

        # Start the processor's worker thread.
        self._request_processor.start_worker()

    # ============================================
    # Clock
    # ============================================

    def get_current_time(self) -> datetime:
        """
        Broker-delivered tick timestamp — the wall-clock time anchor for
        downstream timing logic. In live mode this matches real time
        (no simulation drift).

        Raises:
            RuntimeError: If called before the first tick has arrived
        """
        if self._current_tick is None:
            raise RuntimeError(
                'LiveTradeExecutor.get_current_time() called before first tick'
            )
        return self._current_tick.timestamp

    # ============================================
    # Pending Order Processing (live-specific)
    # ============================================

    def heartbeat(self) -> None:
        """
        Side-effect-free drain for idle ticks (#320 override).

        Drains async worker responses (fills, edits, cancels, query results,
        trades) and processes timeouts. Called by the AutoTrader tick loop
        on queue.Empty so the live pipeline stays responsive even when the
        market is quiet. Does NOT touch tick state — see the abstract
        contract in AbstractTradeExecutor.heartbeat.
        """
        self._request_processor.drain_inbox()
        for pending in self._request_processor.check_timeouts():
            self._handle_timeout(pending)

    def _process_pending_orders(self) -> None:
        """
        Poll broker for pending order updates and handle timeouts.

        Phase 0: Drain async worker responses (idle in V1; carries
                 fills/rejections from the worker thread once async
                 dispatch is activated in a later refactor step).
        Phase 1: Poll LiveRequestProcessor (MARKET orders in transit)
        Phase 2: Poll active limit/stop orders for broker fills

        For each pending order:
        1. Check adapter for status update (filled/rejected/pending)
        2. On fill: call inherited _fill_open_order() / _fill_close_order()
        3. On rejection: record in _order_history
        4. On timeout: record rejection with BROKER_ERROR reason
        """
        # === Phase 0: Drain async worker responses (no-op in V1) ===
        self._request_processor.drain_inbox()

        # === Phase 1: LiveRequestProcessor (MARKET orders in transit) ===
        if self._request_processor.has_pending_orders():
            pending_orders = self._request_processor.get_pending_orders()
            for pending in pending_orders:
                if not pending.broker_ref:
                    continue

                response = self._request_processor.query_order_sync(
                    pending.broker_ref, self.broker.adapter)
                self._handle_broker_response(pending, response)

            # Check for timeouts (orders that broker never responded to)
            timed_out = self._request_processor.check_timeouts()
            for pending in timed_out:
                self._handle_timeout(pending)

        # === Phase 2: Active limit/stop orders (broker-side, waiting for trigger) ===
        self._process_active_orders()

    def _handle_broker_response(
        self,
        pending: PendingOrder,
        response: BrokerResponse,
    ) -> None:
        """
        Process a broker status response for a pending order.

        Args:
            pending: The pending order being checked
            response: Broker's current status response
        """
        if response.status == BrokerOrderStatus.FILLED:
            filled = self._request_processor.mark_filled(
                broker_ref=pending.broker_ref,
                fill_price=response.fill_price,
                filled_lots=response.filled_lots,
            )
            if filled is None:
                return

            # Record pending outcome (latency = time from submission to fill)
            latency_ms = self._calculate_pending_latency_ms(filled)
            self._request_processor.record_outcome(
                filled, PendingOrderOutcome.FILLED, latency_ms=latency_ms)

            # Call inherited fill processing (synthesizes pending.trades
            # entry inside _fill_open_order/close_order if not yet populated)
            if filled.order_action == PendingOrderAction.OPEN:
                self._fill_open_order(filled, fill_price=response.fill_price)
            elif filled.order_action == PendingOrderAction.CLOSE:
                self._fill_close_order(filled, fill_price=response.fill_price)

        elif response.status == BrokerOrderStatus.REJECTED:
            rejected = self._request_processor.mark_rejected(
                broker_ref=pending.broker_ref,
                reason=response.rejection_reason or "broker_rejected",
            )
            if rejected is None:
                return

            # Record pending outcome
            latency_ms = self._calculate_pending_latency_ms(rejected)
            self._request_processor.record_outcome(
                rejected, PendingOrderOutcome.REJECTED, latency_ms=latency_ms)

            # Record rejection in order history
            self._orders_rejected += 1
            rejection = create_rejection_result(
                order_id=rejected.pending_order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=f"Broker rejected: {response.rejection_reason or 'unknown'}",
            )
            self._order_history.append(rejection)
            self._notify_outcome(rejected.direction, rejection, rejected)

        # PENDING / PARTIALLY_FILLED: no action, keep polling

    def _handle_timeout(self, pending: PendingOrder) -> None:
        """
        Handle a timed-out order. Remove from tracker, record rejection.

        Args:
            pending: The timed-out pending order
        """
        # Try to cancel at broker via Tier-3-decoupled sync orchestrator
        if pending.broker_ref:
            try:
                self._request_processor.cancel_order_sync(
                    broker_ref=pending.broker_ref,
                    adapter=self.broker.adapter,
                )
            except Exception as e:
                self.logger.warning(
                    f"Failed to cancel timed-out order {pending.pending_order_id}: {e}"
                )

        # Record pending outcome as TIMED_OUT
        latency_ms = self._calculate_pending_latency_ms(pending)
        self._request_processor.record_outcome(
            pending, PendingOrderOutcome.TIMED_OUT, latency_ms=latency_ms)

        # Remove from tracker
        self._request_processor.mark_rejected(
            broker_ref=pending.broker_ref,
            reason="order_timeout",
        )

        # Record timeout as rejection
        self._orders_rejected += 1
        rejection = create_rejection_result(
            order_id=pending.pending_order_id,
            reason=RejectionReason.BROKER_ERROR,
            message=f"Order timed out after {self._timeout_config.order_timeout_seconds}s",
        )
        self._order_history.append(rejection)
        self._notify_outcome(pending.direction, rejection, pending)

        self.logger.warning(
            f"Order {pending.pending_order_id} timed out "
            f"(broker_ref={pending.broker_ref})"
        )

    # ============================================
    # Async Submit Outcome Handler (called by processor.drain_inbox)
    # ============================================

    def _record_async_rejection(
        self,
        direction: OrderDirection,
        rejection: OrderResult,
    ) -> None:
        """
        Record a rejection delivered by processor.drain_inbox().

        Runs on the main thread when the worker reports a broker-side
        rejection for an async submit. Updates the rejection counter,
        appends to order history, and notifies registered listeners
        (OrderGuard, DriftAuditor #327, future Reconciliation #151).

        Pre-submit / pre-broker-ref rejections have no PendingOrder context,
        so the listener pending param is None at this site.

        Args:
            direction: Order direction (LONG/SHORT) of the rejected order
            rejection: The OrderResult with status=REJECTED
        """
        self._orders_rejected += 1
        self._order_history.append(rejection)
        self._notify_outcome(direction, rejection, None)

    def _handle_limit_submit_response(
        self,
        order_id: str,
        response: BrokerResponse,
    ) -> None:
        """
        Drain-inbox hook for LIMIT submit responses.

        LIMIT orders live in _active_limit_orders (Hybrid pattern shared
        with sim) rather than in the processor's _pending_orders dict.
        When the worker delivers a SubmitResponse for a LIMIT order, the
        processor delegates here so we can update that list directly.

        Three branches:
          REJECTED → remove from _active_limit_orders + record rejection
          FILLED   → remove + _fill_open_order (sync-fill broker, rare)
          PENDING  → confirm broker_ref (polling Phase 2 takes over)

        Args:
            order_id: Internal order id (matches PendingOrder.pending_order_id)
            response: BrokerResponse from the worker
        """
        pending = None
        for p in self._active_limit_orders:
            if p.pending_order_id == order_id:
                pending = p
                break
        if pending is None:
            self.logger.warning(
                f"drain_inbox: LIMIT SubmitResponse for unknown order_id {order_id}"
            )
            return

        if response.is_rejected:
            self._active_limit_orders.remove(pending)
            rejection = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=f"Broker rejected LIMIT: {response.rejection_reason or 'unknown'}",
            )
            self._record_async_rejection(pending.direction, rejection)
            return

        # Non-rejected: confirm broker_ref on the active-limit pending
        pending.broker_ref = response.broker_ref

        if response.is_filled:
            # Sync-fill LIMIT (rare — e.g. price already crossed at submit)
            self._active_limit_orders.remove(pending)
            self._fill_open_order(pending, fill_price=response.fill_price)
        # else: PENDING — pending stays in _active_limit_orders,
        # _process_active_orders Phase 2 polls it for fills.

    # ============================================
    # Async Modify / Cancel / Position-Modify Drain Handlers (#318)
    # ============================================

    def _find_active_order(self, order_id: str) -> Optional[PendingOrder]:
        """Find a PendingOrder by order_id in _active_limit_orders / _active_stop_orders."""
        for p in self._active_limit_orders:
            if p.pending_order_id == order_id:
                return p
        for p in self._active_stop_orders:
            if p.pending_order_id == order_id:
                return p
        return None

    def _handle_modify_response(
        self,
        order_id: str,
        response: BrokerResponse,
    ) -> None:
        """
        Drain-inbox hook for EditResponse (modify-limit / modify-stop).

        On success: apply the provisional ModificationRequest to the
        PendingOrder's entry_price + order_kwargs, swap broker_ref if
        the broker returned a new one (Kraken EditOrder semantic).
        On rejection: discard the provisional values, record rejection.

        In both cases: clear in_flight_operation on the target.
        """
        pending = self._find_active_order(order_id)
        if pending is None:
            self.logger.warning(
                f"drain_inbox: EditResponse for unknown order_id {order_id}"
            )
            return

        mod = pending.pending_modification

        if response.is_rejected:
            self.logger.warning(
                f"Broker rejected modify for {order_id}: "
                f"{response.rejection_reason or 'unknown'}"
            )
            rejection = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=f"Modify rejected: {response.rejection_reason or 'unknown'}",
            )
            # Record without removing from active list — the order is still
            # working at the broker, just the modify failed.
            self._orders_rejected += 1
            self._order_history.append(rejection)
            self._notify_outcome(pending.direction, rejection, pending)
        else:
            # Success — apply provisional values to local shadow state
            if mod is not None:
                if mod.new_price is not None:
                    pending.entry_price = mod.new_price
                if pending.order_kwargs is None:
                    pending.order_kwargs = {}
                if mod.new_limit_price is not None:
                    pending.order_kwargs['limit_price'] = mod.new_limit_price
                if mod.new_stop_loss is not None:
                    pending.order_kwargs['stop_loss'] = mod.new_stop_loss
                if mod.new_take_profit is not None:
                    pending.order_kwargs['take_profit'] = mod.new_take_profit

            # Kraken EditOrder returns a new broker_ref on success
            if response.broker_ref and response.broker_ref != pending.broker_ref:
                self._request_processor.update_broker_ref(
                    old_ref=pending.broker_ref, new_ref=response.broker_ref,
                )
                pending.broker_ref = response.broker_ref

            self.logger.info(
                f"✏️ Order {order_id} modify resolved "
                f"(broker_ref={pending.broker_ref})"
            )

        # Clear in-flight state in all cases (success or rejection)
        pending.in_flight_operation = PendingOperation.NONE
        pending.pending_modification = None

    def _handle_cancel_response(
        self,
        order_id: str,
        response: BrokerResponse,
    ) -> None:
        """
        Drain-inbox hook for CancelResponse.

        On success: remove the order from its active list, fire EXPIRED-style
        outcome notification (algo learns the order is gone).
        On rejection: most often a race condition (order filled before cancel
        reached broker). Surface as informational — the regular poll cycle
        will pick up the actual terminal state (FILLED).
        """
        pending = self._find_active_order(order_id)
        if pending is None:
            self.logger.warning(
                f"drain_inbox: CancelResponse for unknown order_id {order_id}"
            )
            return

        if response.is_rejected:
            # Cancel-during-fill race or other broker rejection — log,
            # clear in-flight, but leave order in active list for the next
            # poll cycle to determine the actual state.
            self.logger.warning(
                f"Broker rejected cancel for {order_id}: "
                f"{response.rejection_reason or 'unknown'} (cancel-race possible)"
            )
            pending.in_flight_operation = PendingOperation.NONE
            return

        # Success — remove from active list. No order_history append: the
        # algo's discipline pattern (has_pending_orders / has_in_flight_operation)
        # observes the state transition naturally. Order_history is reserved
        # for EXECUTED / REJECTED-by-broker, not for algo-initiated cancels.
        if pending in self._active_limit_orders:
            self._active_limit_orders.remove(pending)
        elif pending in self._active_stop_orders:
            self._active_stop_orders.remove(pending)

        pending.in_flight_operation = PendingOperation.NONE
        self.logger.info(
            f"❌ Order {order_id} cancel resolved (broker_ref={pending.broker_ref})"
        )

    def _handle_position_modify_response(
        self,
        position_id: str,
        response: BrokerResponse,
    ) -> None:
        """
        Drain-inbox hook for PositionModifyResponse (#318, native_position_sl_tp=True).

        On success: apply SL/TP changes to portfolio.modify_position.
        On rejection: discard the provisional state.

        In both cases: clear the executor-side tracker for this position.
        """
        mod = self._pending_position_modifications.pop(position_id, None)
        if mod is None:
            self.logger.warning(
                f"drain_inbox: PositionModifyResponse for unknown position_id {position_id}"
            )
            return

        if response.is_rejected:
            self.logger.warning(
                f"Broker rejected position modify for {position_id}: "
                f"{response.rejection_reason or 'unknown'}"
            )
            return

        # Apply to portfolio
        self.portfolio.modify_position(
            position_id=position_id,
            new_stop_loss=mod.new_stop_loss,
            new_take_profit=mod.new_take_profit,
        )
        self.logger.info(
            f"✏️ Position {position_id} modify resolved "
            f"(sl={mod.new_stop_loss}, tp={mod.new_take_profit})"
        )

    # ============================================
    # Trade Records Drain Handler (#326)
    # ============================================

    def submit_trades_query_async(
        self,
        order_id: str,
        broker_ref: str,
    ) -> None:
        """
        Trigger an async per-execution trades query (#326) for a filled order.

        Public delegating wrapper around the request processor's async path.
        Used by DriftAuditor (#327) and future Reconciliation consumers (#151)
        that need broker-truth per-execution detail after a fill.

        The roundtrip runs on the worker thread; the response surfaces via
        drain_inbox to _handle_trades_response which then fans out to all
        registered consumers.

        Args:
            order_id: Internal order_id (matches PendingOrder.pending_order_id)
            broker_ref: Broker-side order reference (Kraken txid, MT5 ticket)
        """
        self._request_processor.submit_trades_query_async(
            order_id=order_id,
            broker_ref=broker_ref,
            adapter=self.broker.adapter,
        )

    def add_trades_response_consumer(
        self,
        consumer: Callable[[TradesQueryResponse], None],
    ) -> None:
        """
        Register an additional consumer for TradesQueryResponse fan-out (#327).

        Called by DriftAuditor and future Reconciliation (#151) to observe
        every trades-query response that arrives in the inbox. Fan-out runs
        AFTER the executor's own resolution logic (append trades to pending,
        finalize fill if applicable) and is executed inside a try/except so
        that one bad consumer cannot break the chain or kill the executor.

        Consumers receive a copy of the response on success AND failure paths
        — failure visibility is required so post-fill state-trackers (e.g.
        DriftAuditor's pending_audits dict) can clean up entries that will
        never be resolved.

        Args:
            consumer: Function receiving (TradesQueryResponse). Read-only
                contract — consumers MUST read from response.trades (immutable),
                NOT from pending.trades (mutation-order-sensitive across the
                executor's own logic).
        """
        self._trades_response_consumers.append(consumer)

    def _handle_trades_response(self, response: TradesQueryResponse) -> None:
        """
        Drain-inbox hook for TradesQueryResponse (#326) — the post-drain
        distribution flow anchor (see ISSUE_326 §8).

        Appends per-execution BrokerTrade records to the parent
        PendingOrder.trades, updates cumulative_* aggregates. If the order
        is still in _active_limit_orders, finalizes the fill via
        _fill_open_order with the cumulative truth.

        In V1, the polling paths (_handle_broker_response, _process_active_orders)
        synthesize a single BrokerTrade inline before _fill_open_order, so the
        order is already removed from active state by the time async trades_query
        responses arrive (if any). The drain handler then logs and skips.

        Tests bypass polling and use the async path directly, which lands here
        with the order still in _active_limit_orders — the full §8 distribution
        runs in that case.

        #327 — fan-out to registered consumers runs in finally so consumers
        see every response (including failures and stale-broker_ref discards)
        regardless of which executor branch was taken. One bad consumer must
        not kill the chain.

        Args:
            response: TradesQueryResponse from the worker thread
        """
        try:
            if not response.success:
                self.logger.warning(
                    f"TradesQueryResponse error for {response.order_id}: "
                    f"{response.error_message or 'unknown'}"
                )
                return

            pending = self._find_active_order(response.order_id)
            if pending is None:
                # Order likely already finalized via sync polling path. The trades
                # data arrived too late to influence the fill. Future #320 async
                # polling will keep the order alive until trades arrive.
                self.logger.debug(
                    f"drain_inbox: TradesQueryResponse for {response.order_id} "
                    f"(order already finalized — V1 sync polling path)"
                )
                return

            # Stale-response guard — broker_ref may have flipped via EditOrder
            if pending.broker_ref != response.broker_ref:
                self.logger.debug(
                    f"Discarding stale trades response for {response.order_id} "
                    f"(response.broker_ref={response.broker_ref} != "
                    f"pending.broker_ref={pending.broker_ref})"
                )
                return

            # Append each broker trade — updates cumulative_*
            for trade in response.trades:
                pending.append_trade(trade)

            # Finalize the fill if cumulative volume populated (post-§8 distribution)
            if pending.cumulative_filled_lots > 0:
                self._active_limit_orders.remove(pending)
                self._fill_open_order(
                    pending,
                    fill_price=pending.cumulative_avg_price,
                    entry_type=EntryType.LIMIT,
                    fill_type=FillType.LIMIT,
                )
                self.logger.info(
                    f"🎯 Order {pending.pending_order_id} filled via trades drain "
                    f"at avg {pending.cumulative_avg_price:.5f} "
                    f"({len(pending.trades)} trade(s), "
                    f"cumulative_lots={pending.cumulative_filled_lots})"
                )
        finally:
            # #327 — Multi-consumer fan-out. Always runs, regardless of
            # success/failure or executor's resolution path. Consumers see
            # every response so they can clean up their own tracking state.
            for consumer in self._trades_response_consumers:
                try:
                    consumer(response)
                except Exception as e:
                    self.logger.error(
                        f"trades_response consumer raised: {e}",
                        exc_info=True,
                    )

    # ============================================
    # Active Order Processing (broker-accepted, waiting for trigger)
    # ============================================

    def _process_active_orders(self) -> None:
        """
        Schedule async status polls for active LIMIT orders.

        Active limit orders are broker-accepted orders waiting for a price
        trigger (shadow state). For each order whose throttle window has
        elapsed and that has no in-flight query, this enqueues a QueryJob
        to the worker thread. The worker performs the broker roundtrip;
        the response is consumed on the main thread in _handle_query_response
        (via drain_inbox / heartbeat).

        Three gates, all silent skips:
          - no broker_ref yet (submit-in-flight window)
          - in_flight_query (a previous poll has not returned yet)
          - inside throttle window (last_polled_at_ms + poll_interval_ms > now)

        Pathological "stuck in-flight" cases are caught by check_timeouts().
        """
        if not self._active_limit_orders:
            return

        now_ms = time.time() * 1000.0
        for pending in self._active_limit_orders:
            if not pending.broker_ref:
                continue
            if pending.in_flight_query:
                continue
            if now_ms - pending.last_polled_at_ms < self._poll_interval_ms:
                continue

            pending.last_polled_at_ms = now_ms
            pending.in_flight_query = True
            self._request_processor.submit_query_order_async(
                order_id=pending.pending_order_id,
                broker_ref=pending.broker_ref,
                adapter=self.broker.adapter,
            )

    def _handle_query_response(self, response: QueryResponse) -> None:
        """
        Drain-inbox hook for QueryResponse (#320).

        Always clears pending.in_flight_query first — the dispatched query is
        resolved regardless of the next steps. Then applies the stale-broker_ref
        guard (broker_ref may have flipped via EditOrder while the query was
        in flight); on stale, returns silently without state mutation. Otherwise
        branches on broker status:
          - FILLED          → _fill_open_order + remove from _active_limit_orders
          - terminal        → rejection + remove
          - PENDING / PARTIALLY_FILLED → keep in list (next throttle cycle re-polls)
        """
        order_id = response.order_id
        broker_response = response.broker_response

        pending = self._find_active_order(order_id)
        if pending is None:
            self.logger.warning(
                f"drain_inbox: QueryResponse for unknown order_id {order_id}"
            )
            return

        # ALWAYS clear in_flight_query — query is resolved
        pending.in_flight_query = False

        # Stale-broker_ref guard: response is against a ref that's no longer
        # the authoritative one (Kraken EditOrder flipped it). Skip state
        # mutation; next throttle cycle will fire a fresh query against the
        # current ref.
        if pending.broker_ref != broker_response.broker_ref:
            self.logger.debug(
                f"QueryResponse stale broker_ref for {order_id}: "
                f"response={broker_response.broker_ref} current={pending.broker_ref}"
            )
            return

        if broker_response.status == BrokerOrderStatus.FILLED:
            self._active_limit_orders.remove(pending)
            self._fill_open_order(
                pending,
                fill_price=broker_response.fill_price,
                entry_type=EntryType.LIMIT,
                fill_type=FillType.LIMIT,
            )
            self.logger.info(
                f"🎯 Active limit order {order_id} filled at "
                f"{broker_response.fill_price} (broker_ref={pending.broker_ref})"
            )
        elif broker_response.is_terminal:
            # REJECTED / CANCELLED / EXPIRED by broker
            self._active_limit_orders.remove(pending)
            self._orders_rejected += 1
            rejection = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=f"Broker {broker_response.status.value}: "
                        f"{broker_response.rejection_reason or 'unknown'}",
            )
            self._order_history.append(rejection)
            self._notify_outcome(pending.direction, rejection, pending)
            self.logger.warning(
                f"Active limit order {order_id} "
                f"{broker_response.status.value} by broker "
                f"(broker_ref={pending.broker_ref})"
            )
        # else: PENDING / PARTIALLY_FILLED — no state change, next cycle re-polls

    # ============================================
    # Order Submission (live-specific)
    # ============================================

    def open_order(self, request: OpenOrderRequest) -> OrderResult:
        """
        Send order to broker for execution.

        Validates parameters, sends to broker via adapter, tracks in
        LiveRequestProcessor. MARKET and LIMIT orders supported.

        Args:
            request: OpenOrderRequest with all order parameters

        Returns:
            OrderResult with PENDING, EXECUTED, or REJECTED status
        """
        self._orders_sent += 1
        self._order_counter += 1
        order_id = self.portfolio.get_next_position_id(request.symbol)

        # Feature gate: only MARKET and LIMIT orders
        if request.order_type not in (OrderType.MARKET, OrderType.LIMIT):
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
                message=f"Order type {request.order_type.value} not supported in live",
            )
            self._order_history.append(result)
            return result

        # Validate order parameters
        is_valid, error = self.broker.validate_order(
            request.symbol, request.lots)
        if not is_valid:
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.INVALID_LOT_SIZE,
                message=error,
            )
            self._order_history.append(result)
            return result

        # Build order kwargs for adapter and tracker
        order_kwargs = {}
        if request.stop_loss is not None:
            order_kwargs["stop_loss"] = request.stop_loss
        if request.take_profit is not None:
            order_kwargs["take_profit"] = request.take_profit
        if request.comment:
            order_kwargs["comment"] = request.comment
        if request.order_type == OrderType.LIMIT and request.price is not None:
            order_kwargs["price"] = request.price

        # MARKET: async submit via the processor worker thread.
        # 1) Register the pending in the processor with broker_ref=None
        #    so has_pending_orders() blocks the algo during the in-flight
        #    window. 2) Enqueue the SubmitJob. 3) Return PENDING right away.
        # The worker pushes a SubmitResponse to _http_inbox; drain_inbox()
        # on the next tick confirms the broker_ref (or applies fill / reject).
        if request.order_type == OrderType.MARKET:
            self._request_processor.register_pending_open(
                order_id=order_id,
                symbol=request.symbol,
                direction=request.direction,
                lots=request.lots,
                broker_ref=None,
                order_kwargs=order_kwargs,
            )
            self._request_processor.submit_open_order_async(
                order_id=order_id,
                symbol=request.symbol,
                direction=request.direction,
                lots=request.lots,
                order_type=OrderType.MARKET,
                adapter=self.broker.adapter,
                **order_kwargs,
            )
            result = OrderResult(
                order_id=order_id,
                status=OrderStatus.PENDING,
                position_id=None,
                metadata={
                    "symbol": request.symbol,
                    "direction": request.direction.value,
                    "lots": request.lots,
                    "broker_ref": None,
                },
            )
            self._order_history.append(result)
            return result

        # LIMIT: async submit via the processor worker thread.
        # Storage stays in _active_limit_orders (Hybrid pattern shared
        # with sim — the resting-order list is conceptually identical in
        # both pipelines). The processor's drain_inbox routes the LIMIT
        # SubmitResponse back via _handle_limit_submit_response so the
        # broker_ref gets confirmed (or the entry removed on rejection /
        # filled on sync-fill).
        # 1) Append placeholder PendingOrder with broker_ref=None so the
        #    algo's has_pending_orders() blocks during the in-flight window.
        pending = PendingOrder(
            pending_order_id=order_id,
            order_action=PendingOrderAction.OPEN,
            order_type=OrderType.LIMIT,
            submitted_at=datetime.now(timezone.utc),
            broker_ref=None,
            symbol=request.symbol,
            direction=request.direction,
            lots=request.lots,
            entry_price=request.price,
            entry_time=datetime.now(timezone.utc),
            order_kwargs=order_kwargs,
        )
        self._active_limit_orders.append(pending)

        # 2) Enqueue the SubmitJob — worker handles HTTP, drain_inbox
        #    routes the response to _handle_limit_submit_response.
        self._request_processor.submit_open_order_async(
            order_id=order_id,
            symbol=request.symbol,
            direction=request.direction,
            lots=request.lots,
            order_type=OrderType.LIMIT,
            adapter=self.broker.adapter,
            **order_kwargs,
        )

        # 3) Return PENDING immediately; broker_ref set later by drain
        result = OrderResult(
            order_id=order_id,
            status=OrderStatus.PENDING,
            position_id=None,
            metadata={
                "symbol": request.symbol,
                "direction": request.direction.value,
                "lots": request.lots,
                "broker_ref": None,
            },
        )
        self._order_history.append(result)
        return result

    # ============================================
    # Close Commands (live-specific)
    # ============================================

    def close_position(
        self,
        position_id: str,
        lots: Optional[float] = None,
    ) -> OrderResult:
        """
        Send close order to broker.

        Args:
            position_id: Position to close
            lots: Lots to close (None = close all)

        Returns:
            OrderResult with PENDING or REJECTED status
        """
        # Check position exists in portfolio
        position = self.portfolio.get_position(position_id)
        if not position:
            return create_rejection_result(
                order_id=f"close_{position_id}",
                reason=RejectionReason.BROKER_ERROR,
                message=f"Position {position_id} not found",
            )

        # Send close to broker — close = reverse direction order
        close_direction = (
            OrderDirection.SHORT if position.direction == OrderDirection.LONG
            else OrderDirection.LONG
        )
        close_lots = lots if lots else position.lots

        # Close is always a MARKET order — async submit via worker.
        # 1) Register pending close with broker_ref=None
        # 2) Enqueue the SubmitJob
        # 3) Return PENDING; drain_inbox() applies the fill on the next tick
        self._request_processor.register_pending_close(
            position_id=position_id,
            broker_ref=None,
            close_lots=close_lots,
        )
        self._request_processor.submit_close_order_async(
            position_id=position_id,
            symbol=position.symbol,
            close_direction=close_direction,
            close_lots=close_lots,
            adapter=self.broker.adapter,
        )

        return OrderResult(
            order_id=position_id,
            status=OrderStatus.PENDING,
            position_id=None,
            executed_lots=close_lots,
            execution_time=datetime.now(timezone.utc),
            metadata={"awaiting_fill": True, "broker_ref": None},
        )

    # ============================================
    # Position Modification (#318) — capability-gated dual-mode
    # ============================================

    def modify_position(
        self,
        position_id: str,
        new_stop_loss=UNSET,
        new_take_profit=UNSET,
    ) -> ModificationResult:
        """
        Modify position SL/TP — capability-gated dual-mode (#318, symmetric to TradeSimulator).

        Routing depends on adapter capability `native_position_sl_tp`:
        - True  (e.g. MT5 in #209): async-pending pattern. Track in
                _pending_position_modifications, enqueue PositionModifyJob,
                drain_inbox applies via portfolio.modify_position. Returns PENDING.
        - False (e.g. Kraken Spot): synchronous fallback to base-class
                portfolio.modify_position (current behavior — Kraken has no
                native attached SL/TP, so the local-only path is correct).
        """
        caps = self.broker.adapter.get_order_capabilities()
        if not caps.native_position_sl_tp:
            # Synchronous fallback — Kraken-style local-only update
            return self.portfolio.modify_position(
                position_id=position_id,
                new_stop_loss=new_stop_loss,
                new_take_profit=new_take_profit,
            )

        # Async path — adapter declared native SL/TP support (#209 MT5)
        position = self.portfolio.get_position(position_id)
        if position is None:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.POSITION_NOT_FOUND,
            )

        if position_id in self._pending_position_modifications:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.OPERATION_BUSY,
            )

        # Capture effective SL/TP — UNSET → current position value
        effective_sl = position.stop_loss if isinstance(new_stop_loss, _UnsetType) else new_stop_loss
        effective_tp = position.take_profit if isinstance(new_take_profit, _UnsetType) else new_take_profit

        self._pending_position_modifications[position_id] = ModificationRequest(
            new_stop_loss=effective_sl,
            new_take_profit=effective_tp,
            submitted_at=datetime.now(timezone.utc),
        )

        # Enqueue PositionModifyJob — worker thread does the broker roundtrip.
        # Adapter must implement _build_position_modify_payload /
        # _do_request_position_modify / _parse_position_modify_response
        # (or equivalent — #209 finalizes the surface).
        self._request_processor.submit_modify_position_async(
            position_id=position_id,
            symbol=position.symbol,
            new_stop_loss=effective_sl,
            new_take_profit=effective_tp,
            adapter=self.broker.adapter,
        )

        self.logger.info(
            f"✏️ Position {position_id} modify scheduled — "
            f"sl={effective_sl}, tp={effective_tp}"
        )

        return ModificationResult(
            success=True,
            status=ModificationStatus.PENDING,
            order_id=position_id,
        )

    # ============================================
    # Limit Order Modification
    # ============================================

    def modify_limit_order(
        self,
        order_id: str,
        new_price: Union[float, _UnsetType] = UNSET,
        new_stop_loss: Union[float, None, _UnsetType] = UNSET,
        new_take_profit: Union[float, None, _UnsetType] = UNSET
    ) -> ModificationResult:
        """
        Schedule modification of a pending limit order via async pattern (#318).

        Resolves order_id to broker_ref via _active_limit_orders, sets the
        in-flight flag on the target PendingOrder, enqueues an EditJob to the
        worker thread, and returns immediately with status=PENDING. The
        modification is applied to local shadow state when the broker's
        EditResponse arrives on the next drain_inbox.

        Args:
            order_id: Pending limit order ID
            new_price: New limit price (UNSET=keep current)
            new_stop_loss: New SL level (UNSET=no change, None=remove)
            new_take_profit: New TP level (UNSET=no change, None=remove)

        Returns:
            ModificationResult — PENDING on accept, REJECTED with reason on:
                LIMIT_ORDER_NOT_FOUND: order_id not in _active_limit_orders
                ORDER_NOT_CONFIRMED:   broker_ref still None (submit in-flight)
                OPERATION_BUSY:        another modify/cancel already in flight
        """
        # Resolve order_id → target pending in active limit orders
        target_pending = None
        for pending in self._active_limit_orders:
            if pending.pending_order_id == order_id:
                target_pending = pending
                break

        if target_pending is None:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.LIMIT_ORDER_NOT_FOUND)

        # Option A: reject modify-on-unconfirmed-submit (broker_ref still None)
        if target_pending.broker_ref is None:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.ORDER_NOT_CONFIRMED)

        # Busy check — one in-flight operation at a time
        if target_pending.in_flight_operation != PendingOperation.NONE:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.OPERATION_BUSY)

        # Translate UNSET → adapter args (None = no change at the adapter layer)
        adapter_price = None if isinstance(new_price, _UnsetType) else new_price
        adapter_sl = None if isinstance(new_stop_loss, _UnsetType) else new_stop_loss
        adapter_tp = None if isinstance(new_take_profit, _UnsetType) else new_take_profit

        # Mark in-flight on the target and store provisional values.
        # The drain handler (_handle_modify_response) consumes these on
        # successful response and applies them to entry_price / order_kwargs.
        target_pending.in_flight_operation = PendingOperation.PENDING_MODIFY
        target_pending.pending_modification = ModificationRequest(
            new_price=adapter_price,
            new_stop_loss=adapter_sl,
            new_take_profit=adapter_tp,
            submitted_at=datetime.now(timezone.utc),
        )

        # Enqueue EditJob — worker thread does the broker roundtrip
        self._request_processor.submit_modify_order_async(
            order_id=order_id,
            broker_ref=target_pending.broker_ref,
            symbol=target_pending.symbol,
            new_price=adapter_price,
            new_stop_loss=adapter_sl,
            new_take_profit=adapter_tp,
            adapter=self.broker.adapter,
        )

        self.logger.info(
            f"✏️ Limit order {order_id} modify scheduled — "
            f"price={adapter_price}, sl={adapter_sl}, tp={adapter_tp} "
            f"(broker_ref={target_pending.broker_ref})"
        )

        return ModificationResult(
            success=True,
            status=ModificationStatus.PENDING,
            order_id=order_id,
        )

    def modify_stop_order(
        self,
        order_id: str,
        new_stop_price: Union[float, _UnsetType] = UNSET,
        new_limit_price: Union[float, _UnsetType] = UNSET,
        new_stop_loss: Union[float, None, _UnsetType] = UNSET,
        new_take_profit: Union[float, None, _UnsetType] = UNSET
    ) -> ModificationResult:
        """
        Schedule modification of a pending stop order via async pattern (#318).

        Capability-gated: returns ORDER_TYPE_NOT_SUPPORTED if the adapter
        doesn't declare stop_orders or stop_limit_orders. Today (#318) the
        path is wired but _active_stop_orders stays empty in live — the
        SUBMIT path is added by #209 (MT5 Live Adapter).

        Args:
            order_id: Pending stop order ID
            new_stop_price: New trigger price (UNSET=keep current)
            new_limit_price: New limit price for STOP_LIMIT (UNSET=keep current)
            new_stop_loss: New SL level (UNSET=no change, None=remove)
            new_take_profit: New TP level (UNSET=no change, None=remove)

        Returns:
            ModificationResult — PENDING on accept, REJECTED with reason.
        """
        caps = self.broker.adapter.get_order_capabilities()
        if not (caps.stop_orders or caps.stop_limit_orders):
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.ORDER_TYPE_NOT_SUPPORTED)

        target_pending = None
        for pending in self._active_stop_orders:
            if pending.pending_order_id == order_id:
                target_pending = pending
                break

        if target_pending is None:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.STOP_ORDER_NOT_FOUND)

        if target_pending.broker_ref is None:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.ORDER_NOT_CONFIRMED)

        if target_pending.in_flight_operation != PendingOperation.NONE:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.OPERATION_BUSY)

        # Translate UNSET → None at adapter boundary
        adapter_stop = None if isinstance(new_stop_price, _UnsetType) else new_stop_price
        adapter_limit = None if isinstance(new_limit_price, _UnsetType) else new_limit_price
        adapter_sl = None if isinstance(new_stop_loss, _UnsetType) else new_stop_loss
        adapter_tp = None if isinstance(new_take_profit, _UnsetType) else new_take_profit

        # Note: the EditJob currently carries only new_price/new_sl/new_tp.
        # For STOP modify, new_price maps to the stop trigger price; the
        # new_limit_price is stored in pending_modification.new_limit_price
        # for the drain handler to apply to order_kwargs['limit_price'].
        # #209 may extend EditJob with a dedicated new_limit_price slot if
        # MT5's ORDER_MODIFY differentiates the two prices.
        target_pending.in_flight_operation = PendingOperation.PENDING_MODIFY
        target_pending.pending_modification = ModificationRequest(
            new_price=adapter_stop,
            new_limit_price=adapter_limit,
            new_stop_loss=adapter_sl,
            new_take_profit=adapter_tp,
            submitted_at=datetime.now(timezone.utc),
        )

        self._request_processor.submit_modify_order_async(
            order_id=order_id,
            broker_ref=target_pending.broker_ref,
            symbol=target_pending.symbol,
            new_price=adapter_stop,
            new_stop_loss=adapter_sl,
            new_take_profit=adapter_tp,
            adapter=self.broker.adapter,
        )

        self.logger.info(
            f"✏️ Stop order {order_id} modify scheduled — "
            f"stop={adapter_stop}, limit={adapter_limit}, "
            f"sl={adapter_sl}, tp={adapter_tp}"
        )

        return ModificationResult(
            success=True,
            status=ModificationStatus.PENDING,
            order_id=order_id,
        )

    def cancel_limit_order(self, order_id: str) -> bool:
        """
        Schedule cancellation of an active limit order via async pattern (#318).

        Sets in_flight_operation=PENDING_CANCEL on the target PendingOrder
        and enqueues a CancelJob to the worker thread. The order is removed
        from _active_limit_orders only when the broker's CancelResponse
        arrives via drain_inbox.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancellation was scheduled. False if order not found,
            still in submit-in-flight (broker_ref=None), or busy.
        """
        for pending in self._active_limit_orders:
            if pending.pending_order_id != order_id:
                continue
            if pending.broker_ref is None:
                # Option A: reject cancel-on-unconfirmed-submit
                return False
            if pending.in_flight_operation != PendingOperation.NONE:
                return False  # busy

            pending.in_flight_operation = PendingOperation.PENDING_CANCEL
            self._request_processor.submit_cancel_order_async(
                order_id=order_id,
                broker_ref=pending.broker_ref,
                adapter=self.broker.adapter,
            )
            self.logger.info(
                f"❌ Limit order {order_id} cancel scheduled "
                f"(broker_ref={pending.broker_ref})"
            )
            return True
        return False

    def cancel_stop_order(self, order_id: str) -> bool:
        """
        Schedule cancellation of an active stop order via async pattern (#318).

        Capability-gated: returns False if the adapter doesn't declare
        stop_orders or stop_limit_orders support. Today (#318) the path is
        wired but _active_stop_orders stays empty in live — the SUBMIT path
        for STOP orders is added by #209 (MT5 Live Adapter).

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancellation was scheduled. False otherwise.
        """
        caps = self.broker.adapter.get_order_capabilities()
        if not (caps.stop_orders or caps.stop_limit_orders):
            return False

        for pending in self._active_stop_orders:
            if pending.pending_order_id != order_id:
                continue
            if pending.broker_ref is None:
                return False
            if pending.in_flight_operation != PendingOperation.NONE:
                return False

            pending.in_flight_operation = PendingOperation.PENDING_CANCEL
            self._request_processor.submit_cancel_order_async(
                order_id=order_id,
                broker_ref=pending.broker_ref,
                adapter=self.broker.adapter,
            )
            self.logger.info(
                f"❌ Stop order {order_id} cancel scheduled "
                f"(broker_ref={pending.broker_ref})"
            )
            return True
        return False

    # ============================================
    # Pending Order Awareness
    # ============================================

    def has_pipeline_orders(self) -> bool:
        """Check if any orders are in the broker tracker (MARKET orders in transit)."""
        return self._request_processor.has_pending_orders()

    def is_pending_close(self, position_id: str) -> bool:
        """Check if a specific position has a pending close order."""
        return self._request_processor.is_pending_close(position_id)

    def _get_pipeline_count(self) -> int:
        """Get number of orders in the broker tracker."""
        return self._request_processor.get_pending_count()

    def get_pending_stats(self) -> PendingOrderStats:
        """
        Get aggregated pending order statistics from live order tracker.

        Returns:
            PendingOrderStats with ms-based latency metrics + active order snapshots
        """
        stats = self._request_processor.get_pending_stats()
        # latency_queue_count must reflect orders currently in the live
        # pipeline (registered locally, awaiting broker confirmation or fill)
        # so the display's "■ N PENDING" indicator appears between submit and
        # fill. Sim populates this from latency_simulator.get_pending_count();
        # the live equivalent is the processor's _pending_orders dict size.
        stats.latency_queue_count = self._request_processor.get_pending_count()
        self._populate_active_order_snapshots(stats)
        return stats

    # ============================================
    # Helpers
    # ============================================

    @staticmethod
    def _calculate_pending_latency_ms(pending: PendingOrder) -> Optional[float]:
        """
        Calculate pending duration in milliseconds from submitted_at to now.

        Args:
            pending: Pending order with submitted_at timestamp

        Returns:
            Latency in ms, or None if submitted_at not set
        """
        if pending.submitted_at is None:
            return None
        elapsed = datetime.now(timezone.utc) - pending.submitted_at
        return elapsed.total_seconds() * 1000

    # ============================================
    # Cleanup
    # ============================================

    def close_all_remaining_orders(self, current_msc: int = 0) -> None:
        """
        Close all open positions and expire active orders at end of run.

        Three-phase cleanup:
        1. Cancel active limit orders at broker, expire locally (EXPIRED records).
        2. Direct-fill open positions using synthetic PendingOrders.
           These bypass the pending order pipeline entirely — no pending
           created, no FORCE_CLOSED in statistics. This is an internal
           cleanup, not an algo-initiated action.
        3. clear_pending() catches genuine stuck-in-pipeline orders
           (e.g. broker hasn't confirmed a fill yet when session ends).
           These ARE real anomalies and correctly recorded as
           FORCE_CLOSED with reason="scenario_end".

        Args:
            current_msc: Not used in live mode (latency is time-based)
        """
        # Phase 1: Cancel active limit orders at broker and expire locally
        if self._active_limit_orders:
            self.logger.info(
                f"📋 {len(self._active_limit_orders)} active limit orders "
                f"at session end — cancelling at broker")
            for pending in self._active_limit_orders:
                if pending.broker_ref:
                    try:
                        self._request_processor.cancel_order_sync(
                            broker_ref=pending.broker_ref,
                            adapter=self.broker.adapter,
                        )
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to cancel active limit "
                            f"{pending.pending_order_id}: {e}")
            self._expire_active_orders()

        # Phase 2: Direct-fill open positions
        open_positions = self.get_open_positions()
        if open_positions:
            self.logger.warning(
                f"{len(open_positions)} positions remain open — direct-closing (no pending)"
            )
            for pos in open_positions:
                synthetic = self._request_processor.create_synthetic_close_order(
                    pos.position_id)
                self._fill_close_order(
                    synthetic, close_reason=CloseReason.SCENARIO_END)

        # Phase 3: Catch genuine stuck-in-pipeline orders (real anomalies)
        self._request_processor.clear_pending(reason="scenario_end")

        # #318 — clear pending position modifications (live tracker)
        if self._pending_position_modifications:
            self.logger.info(
                f"✏️ {len(self._pending_position_modifications)} pending "
                f"position modifications at scenario end — discarded"
            )
            self._pending_position_modifications.clear()

        # Phase 4: Stop the worker thread cleanly
        self._request_processor.stop_worker()
