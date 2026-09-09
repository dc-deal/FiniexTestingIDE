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
from typing import Callable, Dict, FrozenSet, List, Optional, Set, Tuple, Union

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor, ExecutorMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.trading_env.portfolio_manager import UNSET, _UnsetType
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
    TimeoutConfig,
)
from python.framework.types.live_types.live_request_types import QueryResponse, TradesQueryResponse
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason,
    EntryType,
)
from python.framework.types.trading_env_types.latency_simulator_types import (
    ModificationRequest,
    PendingOperation,
    PendingOrder,
    PendingOrderAction,
    PendingOrderFills,
    PendingOrderOutcome,
    PendingOrderTiming,
)
from python.framework.types.trading_env_types.order_types import (
    FillType,
    ModificationRejectionReason,
    ModificationResult,
    ModificationStatus,
    OpenOrderRequest,
    OrderAction,
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
    RejectionReason,
    create_rejection_result,
)
from python.framework.types.trading_env_types.pending_order_stats_types import PendingOrderStats
from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.utils.run_id_utils import build_client_order_id


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
        rest_ladder: Optional[ConnectionLadder] = None,
        session_key: str = '',
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
            rest_ladder: Retry/give-up decision for the broker's REST endpoint (#473),
                shared with the Reconciler. Defaults to the standard policy.
            session_key: Short discriminator this session stamps onto every client order
                id it sends (#473). Empty disables the wire key — mock and dry-run paths
                that never reach a venue do not need one.
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
        # A resting order whose submit answer never arrived has no reference to poll with and
        # is not covered by check_timeouts (that sees the processor's dict only). It is kept
        # deliberately (#473) — this is only how long we wait before SAYING so, once per order.
        self._unresolved_report_after_s: float = (
            self._timeout_config.order_timeout_seconds)
        self._reported_unresolved: Set[str] = set()
        # An order the venue answers about by naming nothing is a standing condition too, and
        # it is said once for the same reason (see _handle_query_response).
        self._reported_unknown: Set[str] = set()
        self._session_key = session_key
        # #473 — one ladder for the broker's REST endpoint, shared with the Reconciler so
        # both classify a 502 the same way. A transport fault must never reach the trading
        # logic wearing the face of a venue rejection.
        self._rest_ladder = rest_ladder or ConnectionLadder(
            name='broker_rest',
            policy=ConnectionPolicy(),
            logger=logger,
        )

        # Live order tracker with broker ref tracking
        self._request_processor = LiveRequestProcessor(
            logger=logger,
            timeout_config=self._timeout_config,
            rest_ladder=self._rest_ladder,
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
            f'LiveTradeExecutor initialized with broker: '
            f'{broker_config.get_broker_name()} '
            f'(timeout={self._timeout_config.order_timeout_seconds}s)'
        )

        # Wire the processor's drain-inbox hooks into the executor.
        # All hooks run on the main thread inside drain_inbox().
        # _fill_open_order / _fill_close_order already append the
        # EXECUTED result to order_history and notify listeners, so
        # they can be used directly as hooks — no wrapper needed.
        # resting_response routes LIMIT / STOP / STOP_LIMIT submit responses back here so
        # we can update _active_limit_orders / _active_stop_orders (Hybrid pattern —
        # shared storage).
        # #318 — modify/cancel/position-modify responses route to handlers
        # that mutate _active_*_orders / portfolio (Hybrid pattern).
        self._request_processor.set_executor_hooks(
            fill_open=self._fill_open_order,
            fill_close=self._fill_close_order,
            on_rejection=self._record_async_rejection,
            resting_response=self._handle_resting_submit_response,
            modify_response=self._handle_modify_response,
            cancel_response=self._handle_cancel_response,
            position_modify_response=self._handle_position_modify_response,
            trades_response=self._handle_trades_response,
            query_response=self._handle_query_response,
        )

        # Start the processor's worker thread.
        self._request_processor.start_worker()

    def get_rest_ladder(self) -> ConnectionLadder:
        """
        The retry/give-up decision for the broker's REST endpoint (#473).

        Shared rather than per-caller: the Reconciler's truth pull and the order path
        talk to the same endpoint, and two classifications of one 502 is exactly the
        drift this ladder exists to remove.

        Returns:
            The ConnectionLadder this executor was built with
        """
        return self._rest_ladder

    def pull_broker_balances(self) -> Optional[Dict[str, float]]:
        """
        The venue's FULL balance sheet, under the shared REST ladder (§43).

        Unfiltered on purpose, quote currency included: a FLATNESS answer is not a balance
        sheet, and reusing one as the other is how the Field Study's truth plane came to
        record the two balances that had not moved while omitting the one that had (#506).
        Whoever needs "is this account flat" asks the Reconciler; whoever needs "what does
        the venue hold" asks here.

        It lives on the executor because this is the only place that owns both the adapter
        and the ladder — the pairing was previously re-derived by every caller, which is
        also why the flat check itself has no ladder at all.

        Returns:
            Asset → amount as the venue reports it, or None when the ladder gave up
        """
        return run_with_ladder(
            self.broker.adapter.get_broker_balances, self.get_rest_ladder())

    def get_session_key(self) -> str:
        """
        The discriminator stamped onto every client order id this session sends (#473).

        Returns:
            The session key, or '' when no wire key is stamped
        """
        return self._session_key

    def build_client_order_id(self, order_id: str) -> Optional[str]:
        """
        The key this session sends to the venue for one internal order id.

        The SHAPE of the key lives in run_id_utils beside the discriminator it is built
        from, because #355 reads keys as well as writes them and one format must not be
        spelled out in two places.

        Args:
            order_id: Internal order id, e.g. 'pos_btcusd_47'

        Returns:
            The wire key, e.g. 'p1641_47', or None when no session key is configured
        """
        return build_client_order_id(self._session_key, order_id)

    def get_in_flight_order_ids(self) -> Set[str]:
        """
        Internal ids the latency queue is still waiting on (#355).

        The truth pull compares against the RESTING orders only, so a MARKET or CLOSE order
        in flight has no counterpart there and would read as one we placed and stopped
        tracking. It is tracked — just in the other world. This is how the Reconciler tells
        the two apart.

        Returns:
            The pending order ids currently held by the request processor
        """
        return {p.pending_order_id for p in self._request_processor.get_pending_orders()}

    def adopt_resting_orders(
        self,
        adoptions: List[Tuple[str, BrokerOrder]],
    ) -> None:
        """
        Rebuild shadow pendings for resting broker orders recognised as ours (#355 Phase 2).

        Boot-time counterpart to apply_order_attributions: there the local pending exists and
        only its reference is missing; here nothing local exists at all, because the process
        that placed the order is gone. What makes the reconstruction honest rather than a
        guess is the client order id — the order carries a key this bot minted, so the
        internal id is recovered rather than invented.

        The order lands in the resting-order world with its reference already set, so the
        normal poll path picks it up on the first pass and a fill is processed like any other.

        WHICH resting world depends on the type. A stop is not a limit: its own cancel and
        modify paths look in `_active_stop_orders`, so a stop filed among the limits is
        adoptable but not manageable — `modify_limit_order` would find it and amend its
        TRIGGER as a limit price. `_RESTING_ORDER_TYPES` in the adopter admits STOP and
        STOP_LIMIT on purpose (#500), so this is reachable as soon as one rests.

        Args:
            adoptions: (recovered internal order id, broker order) pairs
        """
        for order_id, broker_order in adoptions:
            is_stop = broker_order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT)
            # Same convention as the simulation: for a stop the resting price IS the
            # trigger, and a stop-limit carries its limit price beside it.
            entry_price = broker_order.stop_price if is_stop else broker_order.price
            order_kwargs = {
                'stop_loss': broker_order.stop_loss,
                'take_profit': broker_order.take_profit,
            }
            if broker_order.order_type == OrderType.STOP_LIMIT:
                order_kwargs['limit_price'] = broker_order.price
            pending = PendingOrder(
                pending_order_id=order_id,
                order_action=PendingOrderAction.OPEN,
                order_type=broker_order.order_type,
                timing=PendingOrderTiming(submitted_at=None),
                broker_ref=broker_order.broker_ref,
                symbol=broker_order.symbol,
                direction=broker_order.direction,
                lots=broker_order.lots,
                entry_price=entry_price,
                # The venue reports the ORIGINAL size and the executed part separately, so an
                # already partly-filled resting order has to be adopted as partly filled.
                # Carrying only `lots` would rebuild it at full size and overstate the shadow
                # by exactly the part that already traded.
                fills=PendingOrderFills(cumulative_filled_lots=broker_order.filled_lots),
                order_kwargs=order_kwargs,
                submission=SubmissionMetadata(),
            )
            if is_stop:
                self._active_stop_orders.append(pending)
            else:
                self._active_limit_orders.append(pending)
            # An adopted order was SENT — by a predecessor of this session, but sent. Counting
            # it keeps the executed/sent ratio meaningful; without it a fill would raise the
            # execution rate above 100 %.
            self._orders_sent += 1
            self.logger.info(
                f'🧬 Adopted resting {broker_order.order_type.value} order {order_id} from '
                f'broker truth (client_order_id={broker_order.client_order_id}, '
                f'broker_ref={broker_order.broker_ref}, {broker_order.direction.value} '
                f'{broker_order.lots} {broker_order.symbol} @ {entry_price})'
            )

    def apply_order_attributions(
        self,
        attributions: List[Tuple[PendingOrder, BrokerOrder]],
    ) -> None:
        """
        Adopt the venue's reference for orders the truth pull recognized as ours (#355).

        The Reconciler detects, the executor writes: a resting order carrying THIS
        session's client order id belongs to a local pending whose submit answer was lost
        (#473), so the reference is not a correction of what we believe — it is the name
        the venue gave the order we already know we placed. Restoring it puts the pending
        back into the poll path, which is what ends the block on has_pending_orders().

        A reference that is already set is never overwritten. That would be a genuine
        correction, and correction is #349's decision, not this one.

        This is the SECOND place a broker_ref goes from None to set, and it therefore owes
        the same duty as the first (_handle_resting_submit_response): a cancel the algo asked
        for while the reference was missing was PARKED, not refused (#361), and the missing
        reference was the only reason it could not be sent. Restoring the reference without
        issuing it would hand the order back to the poll path as if nothing had been asked —
        and a resting order the algo cancelled can still fill.

        Args:
            attributions: (local pending, broker order) pairs matched by client order id
        """
        for pending, broker_order in attributions:
            if pending.broker_ref:
                continue
            if not broker_order.broker_ref:
                # An attribution without a venue reference would leave the pending invisible
                # in BOTH directions: still skipped by the poller (no ref) and no longer
                # PENDING_SUBMIT, so the error-pot report that grades the session never
                # fires. Kraken cannot produce it today; the second adapter (#209) is the
                # reason this is a guard rather than an assumption.
                self.logger.warning(
                    f'🔗 Attribution for {pending.pending_order_id} carries no broker '
                    f'reference (client_order_id={broker_order.client_order_id}) — ignored.'
                )
                continue

            pending.broker_ref = broker_order.broker_ref
            self.logger.info(
                f'🔗 Order {pending.pending_order_id} reclaimed from broker truth '
                f'(client_order_id={broker_order.client_order_id}) — '
                f'broker_ref={broker_order.broker_ref} restored, polling resumed'
            )

            if pending.execution_state.cancel_requested:
                pending.execution_state.cancel_requested = False
                pending.execution_state.in_flight_operation = PendingOperation.PENDING_CANCEL
                self._request_processor.submit_cancel_order_async(
                    order_id=pending.pending_order_id,
                    broker_ref=pending.broker_ref,
                    adapter=self.broker.adapter,
                )
                self.logger.info(
                    f'❌ Limit order {pending.pending_order_id} deferred cancel issued '
                    f'after attribution (broker_ref={pending.broker_ref})'
                )
                continue

            pending.execution_state.in_flight_operation = PendingOperation.NONE

    # ============================================
    # Pending Order Processing (live-specific)
    # ============================================

    def heartbeat(self) -> None:
        """
        Side-effect-free drain for idle ticks (#320 override, #360 re-poll).

        Drains async worker responses (fills, edits, cancels, query results,
        trades), processes timeouts, and re-polls active limit orders. Called by
        the AutoTrader tick loop on queue.Empty so the live pipeline stays
        responsive even when the market is quiet — the fill/cancel-confirm query
        now fires during idle, not only on a real tick (#360). Does NOT touch
        tick state — see the abstract contract in AbstractTradeExecutor.heartbeat.
        """
        self._request_processor.drain_inbox()
        for pending in self._request_processor.check_timeouts():
            self._handle_timeout(pending)
        # #360: re-poll active limit orders on the timer too (was on_tick-only).
        # Per-order throttle (poll_interval_ms) still gates the actual broker I/O.
        self._process_active_orders()

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

            # Call inherited fill processing (synthesizes pending.fills.trades
            # entry inside _fill_open_order/close_order if not yet populated)
            if filled.order_action == PendingOrderAction.OPEN:
                self._fill_open_order(filled, fill_price=response.fill_price)
            elif filled.order_action == PendingOrderAction.CLOSE:
                self._fill_close_order(filled, fill_price=response.fill_price)

        elif response.status == BrokerOrderStatus.REJECTED:
            rejected = self._request_processor.mark_rejected(
                broker_ref=pending.broker_ref,
                reason=response.rejection_reason or 'broker_rejected',
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
            self._check_order_history_limit()
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
                    f'Failed to cancel timed-out order {pending.pending_order_id}: {e}'
                )

        # Record pending outcome as TIMED_OUT
        latency_ms = self._calculate_pending_latency_ms(pending)
        self._request_processor.record_outcome(
            pending, PendingOrderOutcome.TIMED_OUT, latency_ms=latency_ms)

        # Remove from tracker
        self._request_processor.mark_rejected(
            broker_ref=pending.broker_ref,
            reason='order_timeout',
        )

        # Record timeout as rejection. #473 — an order that went UNRESOLVED never got an
        # answer, so BROKER_ERROR would blame the venue for our transport fault. The venue
        # may still hold it; because the submit carried our client order id, the reconcile
        # pull can now attribute that resting order to us, and #349 decides what to do
        # about it.
        unresolved = (
            pending.execution_state.in_flight_operation is PendingOperation.PENDING_SUBMIT)
        self._orders_rejected += 1
        rejection = create_rejection_result(
            order_id=pending.pending_order_id,
            reason=(RejectionReason.BROKER_UNREACHABLE if unresolved
                    else RejectionReason.BROKER_ERROR),
            message=(
                f'Order unresolved for {self._timeout_config.order_timeout_seconds}s — the '
                f'broker never answered. It may hold this order; reconciliation matches it '
                f'by client order id.'
                if unresolved else
                f'Order timed out after {self._timeout_config.order_timeout_seconds}s'),
        )
        self._check_order_history_limit()
        self._order_history.append(rejection)
        self._notify_outcome(pending.direction, rejection, pending)

        self.logger.warning(
            f'Order {pending.pending_order_id} timed out '
            f'(broker_ref={pending.broker_ref})'
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
        self._check_order_history_limit()
        self._order_history.append(rejection)
        self._notify_outcome(direction, rejection, None)

    def _handle_resting_submit_response(
        self,
        order_id: str,
        response: BrokerResponse,
    ) -> None:
        """
        Drain-inbox hook for RESTING submit responses (LIMIT, STOP, STOP_LIMIT).

        Resting orders live in _active_limit_orders / _active_stop_orders (Hybrid pattern
        shared with sim) rather than in the processor's _pending_orders dict. When the
        worker delivers a SubmitResponse for one, the processor delegates here so we can
        update the list that holds it.

        Four branches:
          UNRESOLVED → keep it, mark in-flight (#473 — the venue may hold it)
          REJECTED → remove from its list + record rejection
          FILLED   → remove + _fill_open_order (sync-fill broker, rare)
          PENDING  → confirm broker_ref (polling Phase 2 takes over)

        Args:
            order_id: Internal order id (matches PendingOrder.pending_order_id)
            response: BrokerResponse from the worker
        """
        pending = self._find_active_order(order_id)
        if pending is None:
            self.logger.warning(
                f'drain_inbox: resting SubmitResponse for unknown order_id {order_id}'
            )
            return
        order_label = pending.order_type.value if pending.order_type else 'resting'

        if response.is_unresolved:
            # #473 — no answer is not a refusal. Keep the order and leave broker_ref
            # untouched: overwriting it with the empty ref the failure carries would
            # lose the only handle a later query could use.
            pending.execution_state.in_flight_operation = PendingOperation.PENDING_SUBMIT
            self.logger.error(
                f'📡 {order_label} order {order_id} UNRESOLVED — the broker did not answer '
                f'({response.rejection_reason}). Kept in flight for resolution by query.'
            )
            return

        if response.is_rejected:
            self._drop_active_order(pending)
            rejection = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=(f'Broker rejected {order_label}: '
                         f"{response.rejection_reason or 'unknown'}"),
            )
            self._record_async_rejection(pending.direction, rejection)
            return

        # Non-rejected: confirm broker_ref on the resting pending
        pending.broker_ref = response.broker_ref

        if response.is_filled:
            # Sync-fill (rare — e.g. price already crossed at submit).
            # FILLED-precedence: a fill wins over any deferred cancel (#361).
            self._drop_active_order(pending)
            self._fill_open_order(pending, fill_price=response.fill_price)
        elif pending.execution_state.cancel_requested:
            # Deferred cancel (#361): a cancel was requested while this submit was
            # in-flight (broker_ref=None). Now confirmed → issue it. The CancelResponse
            # resolves via _handle_cancel_response (removes from _active_limit_orders).
            pending.execution_state.cancel_requested = False
            pending.execution_state.in_flight_operation = PendingOperation.PENDING_CANCEL
            self._request_processor.submit_cancel_order_async(
                order_id=order_id,
                broker_ref=pending.broker_ref,
                adapter=self.broker.adapter,
            )
            self.logger.info(
                f'❌ {order_label} order {order_id} deferred cancel issued '
                f'(broker_ref={pending.broker_ref})'
            )
        # else: PENDING — pending stays in its resting list,
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

    @staticmethod
    def _resting_fill_classification(
        pending: PendingOrder
    ) -> Tuple[EntryType, FillType]:
        """
        How a filled resting order is booked — by its own type, not by assumption.

        The three live fill paths hardcoded LIMIT for both, which was right while LIMIT was
        the only resting type live could hold. It decides the FEE: LIMIT and STOP_LIMIT are
        booked as maker, MARKET and STOP as taker, so a filled stop booked as a limit
        understates the cost of every stop entry.

        Args:
            pending: The resting order that filled

        Returns:
            (entry type for the trade record, fill type for the order result)
        """
        if pending.order_type == OrderType.STOP:
            return EntryType.STOP, FillType.STOP
        if pending.order_type == OrderType.STOP_LIMIT:
            return EntryType.STOP_LIMIT, FillType.STOP_LIMIT
        return EntryType.LIMIT, FillType.LIMIT

    def _report_stuck_unresolved(self, pending: PendingOrder) -> None:
        """
        Report a resting order that has no broker reference and no way to get one.

        Said ONCE per order: it is a standing condition, not an event, and repeating it every
        poll cycle would bury the session channel it needs to reach. The order is deliberately
        NOT dropped — #473 exists to stop exactly that, and #487 is what will resolve it by
        asking the venue about our own client order id.

        Args:
            pending: The resting order whose submit answer never arrived
        """
        if pending.execution_state.in_flight_operation != PendingOperation.PENDING_SUBMIT:
            return
        if pending.pending_order_id in self._reported_unresolved:
            return
        submitted_at = pending.timing.submitted_at
        if submitted_at is None:
            return
        waited_s = (datetime.now(timezone.utc) - submitted_at).total_seconds()
        if waited_s < self._unresolved_report_after_s:
            return

        self._reported_unresolved.add(pending.pending_order_id)
        self.logger.error(
            f'📡 Resting {pending.order_type.value if pending.order_type else "order"} '
            f'{pending.pending_order_id} has been UNRESOLVED for {waited_s:.0f}s — the '
            f'submit answer never arrived, so there is no broker reference to poll with. '
            f'It is kept rather than dropped, because the venue may be holding it (#473), '
            f'and it blocks the algo\'s pending gate for as long as it sits here. Check the '
            f'account by hand, or wait for the targeted status query (#487).')

    def _drop_active_order(self, pending: PendingOrder) -> None:
        """
        Remove a resting order from whichever of the two lists holds it.

        Callers that resolve an order by id must not need to know which world it lives in;
        every one of them that hardcoded `_active_limit_orders.remove` was correct only
        while a stop could not rest in live (#500).

        Args:
            pending: The resting order to drop
        """
        if pending in self._active_limit_orders:
            self._active_limit_orders.remove(pending)
        elif pending in self._active_stop_orders:
            self._active_stop_orders.remove(pending)

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
                f'drain_inbox: EditResponse for unknown order_id {order_id}'
            )
            return

        mod = pending.execution_state.pending_modification

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
            self._check_order_history_limit()
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
                f'✏️ Order {order_id} modify resolved '
                f'(broker_ref={pending.broker_ref})'
            )

        # Clear in-flight state in all cases (success or rejection)
        pending.execution_state.in_flight_operation = PendingOperation.NONE
        pending.execution_state.pending_modification = None

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
                f'drain_inbox: CancelResponse for unknown order_id {order_id}'
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
            pending.execution_state.in_flight_operation = PendingOperation.NONE
            return

        # Success — remove from active list. No order_history append: the
        # algo's discipline pattern (has_pending_orders / has_in_flight_operation)
        # observes the state transition naturally. Order_history is reserved
        # for EXECUTED / REJECTED-by-broker, not for algo-initiated cancels.
        self._drop_active_order(pending)

        pending.execution_state.in_flight_operation = PendingOperation.NONE
        self.logger.info(
            f'❌ Order {order_id} cancel resolved (broker_ref={pending.broker_ref})'
        )
        self._emit_order_cancelled(pending)

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
                f'drain_inbox: PositionModifyResponse for unknown position_id {position_id}'
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
            f'✏️ Position {position_id} modify resolved '
            f'(sl={mod.new_stop_loss}, tp={mod.new_take_profit})'
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
                NOT from pending.fills.trades (mutation-order-sensitive across the
                executor's own logic).
        """
        self._trades_response_consumers.append(consumer)

    def _handle_trades_response(self, response: TradesQueryResponse) -> None:
        """
        Drain-inbox hook for TradesQueryResponse (#326) — the post-drain
        distribution flow anchor (see ISSUE_326 §8).

        Appends per-execution BrokerTrade records to the parent
        PendingOrder.fills.trades, updates cumulative_* aggregates. If the order
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
                    f'drain_inbox: TradesQueryResponse for {response.order_id} '
                    f'(order already finalized — V1 sync polling path)'
                )
                return

            # Stale-response guard — broker_ref may have flipped via EditOrder
            if pending.broker_ref != response.broker_ref:
                self.logger.debug(
                    f'Discarding stale trades response for {response.order_id} '
                    f'(response.broker_ref={response.broker_ref} != '
                    f'pending.broker_ref={pending.broker_ref})'
                )
                return

            # Append each broker trade — updates cumulative_*
            for trade in response.trades:
                pending.fills.append_trade(trade)

            # Finalize the fill if cumulative volume populated (post-§8 distribution)
            if pending.fills.cumulative_filled_lots > 0:
                entry_type, _ = self._resting_fill_classification(pending)
                self._drop_active_order(pending)
                self._fill_open_order(
                    pending,
                    fill_price=pending.fills.cumulative_avg_price,
                    entry_type=entry_type,
                    fill_type=FillType.LIMIT,
                )
                self.logger.info(
                    f'🎯 Order {pending.pending_order_id} filled via trades drain '
                    f'at avg {pending.fills.cumulative_avg_price:.5f} '
                    f'({len(pending.fills.trades)} trade(s), '
                    f'cumulative_lots={pending.fills.cumulative_filled_lots})'
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
                        f'trades_response consumer raised: {e}',
                        exc_info=True,
                    )

    # ============================================
    # Active Order Processing (broker-accepted, waiting for trigger)
    # ============================================

    def _process_active_orders(self) -> None:
        """
        Schedule async status polls for every RESTING order (limit and stop).

        Resting orders are broker-accepted orders waiting for a price trigger (shadow
        state). For each order whose throttle window has elapsed and that has no in-flight
        query, this enqueues a QueryJob to the worker thread. The worker performs the broker
        roundtrip; the response is consumed on the main thread in _handle_query_response
        (via drain_inbox / heartbeat).

        Both lists, since #500. Reading `_active_limit_orders` alone was correct only while
        a stop could not rest in live — and a stop that rests unpolled is worse than one
        that never existed: it can fill at the venue while our book still shows it waiting.

        Four gates, all silent skips:
          - no tick has arrived yet (see below)
          - no broker_ref yet (submit-in-flight window)
          - in_flight_query (a previous poll has not returned yet)
          - inside throttle window (last_polled_at_ms + poll_interval_ms > now)

        Pathological "stuck in-flight" cases are caught by check_timeouts().

        WHY THE TICK GATE (#355). A fill cannot be PROCESSED without a tick: `_fill_open_order`
        reads `self._current_tick` for the record and `get_current_price` for bid/ask, and both
        are None before the first one arrives. Every other order reaches this list from a
        decision, which by definition already ran on a tick — so the situation could not occur
        until boot ADOPTION became the first path that puts a fillable order into the shadow
        before any tick exists. Polling one then would answer FILLED into a dereference of
        None, i.e. an uncaught exception in the tick loop of a live session, with a real
        execution at the venue and no local record of it. Waiting for the first tick costs
        nothing: the algo is not running before it either.
        """
        resting = self._active_limit_orders + self._active_stop_orders
        if not resting or self._current_tick is None:
            return

        now_ms = time.time() * 1000.0
        for pending in resting:
            if not pending.broker_ref:
                # #473 kept this order rather than dropping it, which is right: the venue may
                # hold it and calling that a rejection would forget a live order. But nothing
                # ever picks it up again — there is no reference to poll WITH, and
                # check_timeouts only sees the processor's dict, never the resting lists. So
                # it sits here forever while has_pending_orders() stays true and blocks the
                # algo. Reporting it is the least we owe the operator until #487 can ASK the
                # venue by our own client order id. Once per order, not once per tick.
                self._report_stuck_unresolved(pending)
                continue
            if pending.execution_state.in_flight_query:
                continue
            if now_ms - pending.execution_state.last_polled_at_ms < self._poll_interval_ms:
                continue

            pending.execution_state.last_polled_at_ms = now_ms
            pending.execution_state.in_flight_query = True
            self._request_processor.submit_query_order_async(
                order_id=pending.pending_order_id,
                broker_ref=pending.broker_ref,
                adapter=self.broker.adapter,
            )

    def _handle_query_response(self, response: QueryResponse) -> None:
        """
        Drain-inbox hook for QueryResponse (#320).

        Always clears pending.execution_state.in_flight_query first — the dispatched query is
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
                f'drain_inbox: QueryResponse for unknown order_id {order_id}'
            )
            return

        # ALWAYS clear in_flight_query — query is resolved
        pending.execution_state.in_flight_query = False

        # Stale-broker_ref guard: response is against a ref that's no longer
        # the authoritative one (Kraken EditOrder flipped it). Skip state
        # mutation; next throttle cycle will fire a fresh query against the
        # current ref.
        if pending.broker_ref != broker_response.broker_ref:
            self.logger.debug(
                f'QueryResponse stale broker_ref for {order_id}: '
                f'response={broker_response.broker_ref} current={pending.broker_ref}'
            )
            return

        if broker_response.is_unknown:
            # The venue answered and named no such order. It is NOT dropped — dropping on an
            # absence is exactly how an orphan is made, and the venue may hold it after all.
            # Said once per order: a re-poll produces the same non-answer, so repeating it
            # every cycle would bury the session channel this has to reach (§35).
            if order_id not in self._reported_unknown:
                self._reported_unknown.add(order_id)
                self.logger.error(
                    f'❓ The venue answered about {order_id} '
                    f'(broker_ref={pending.broker_ref}) by naming no such order. That is not '
                    f'"still working" — it is an absence, and this order is kept rather than '
                    f'booked or dropped. Check the account by hand; a reference lookup cannot '
                    f'resolve it, only a time-ranged history read can.')
            return

        if broker_response.status == BrokerOrderStatus.FILLED:
            entry_type, fill_type = self._resting_fill_classification(pending)
            self._drop_active_order(pending)
            self._fill_open_order(
                pending,
                fill_price=broker_response.fill_price,
                entry_type=entry_type,
                fill_type=fill_type,
            )
            self.logger.info(
                f'🎯 Active {entry_type.value} order {order_id} filled at '
                f'{broker_response.fill_price} (broker_ref={pending.broker_ref})'
            )
        elif broker_response.is_terminal:
            # REJECTED / CANCELLED / EXPIRED by broker
            self._drop_active_order(pending)
            self._orders_rejected += 1
            rejection = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.BROKER_ERROR,
                message=f"Broker {broker_response.status.value}: "
                        f"{broker_response.rejection_reason or 'unknown'}",
            )
            self._check_order_history_limit()
            self._order_history.append(rejection)
            self._notify_outcome(pending.direction, rejection, pending)
            self.logger.warning(
                f'Active limit order {order_id} '
                f'{broker_response.status.value} by broker '
                f'(broker_ref={pending.broker_ref})'
            )
        # else: PENDING / PARTIALLY_FILLED — no state change, next cycle re-polls

    # ============================================
    # Order Submission (live-specific)
    # ============================================

    def _current_submission(self) -> SubmissionMetadata:
        """
        Snapshot the current tick as submission metadata (#340/#345).

        Returns:
            SubmissionMetadata from the current tick, or empty when no tick
            is in scope (cold-start, heartbeat-only path)
        """
        if self._current_tick is None:
            return SubmissionMetadata()
        return SubmissionMetadata(
            tick_mid_price=self._current_tick.mid,
            tick_time_msc=self._current_tick.time_msc,
        )

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
        request = self._normalize_order_request(request)
        self._orders_sent += 1
        self._order_counter += 1
        order_id = self.portfolio.get_next_position_id(request.symbol)

        # Feature gate — reads the declaration below, so pre-flight and this check agree.
        if request.order_type not in self.get_supported_order_types():
            self._orders_rejected += 1
            result = create_rejection_result(
                order_id=order_id,
                reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
                message=f'Order type {request.order_type.value} not supported in live',
            )
            self._check_order_history_limit()
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
            self._check_order_history_limit()
            self._order_history.append(result)
            return result

        # Funds, net of what this bot's own unfilled orders already claim (#489). The venue
        # reserves at placement, so this must refuse here rather than take the rejection
        # back from the broker.
        funds_rejection = self._reject_if_funds_committed(request, order_id)
        if funds_rejection:
            return funds_rejection

        price_rejection = self._reject_if_resting_prices_invalid(request, order_id)
        if price_rejection:
            return price_rejection

        # Build order kwargs for adapter and tracker
        order_kwargs = {}
        if request.stop_loss is not None:
            order_kwargs['stop_loss'] = request.stop_loss
        if request.take_profit is not None:
            order_kwargs['take_profit'] = request.take_profit
        if request.comment:
            order_kwargs['comment'] = request.comment
        # One name per price, shared with the simulation, so a single reader serves both
        # pipelines: the trigger is `stop_price` and a limit price is `limit_price` —
        # whether it belongs to a LIMIT or to the limit half of a STOP_LIMIT. The LIMIT
        # branch wrote `price` until #500, which is why the reconciler's own comparison
        # found nothing for a live limit order and was inert on the one pipeline it exists
        # for.
        if request.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) \
                and request.price is not None:
            order_kwargs['limit_price'] = request.price
        if request.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            order_kwargs['stop_price'] = request.stop_price

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
                submission=self._current_submission(),
            )
            self._request_processor.submit_open_order_async(
                order_id=order_id,
                symbol=request.symbol,
                direction=request.direction,
                lots=request.lots,
                order_type=OrderType.MARKET,
                adapter=self.broker.adapter,
                client_order_id=self.build_client_order_id(order_id),
                **order_kwargs,
            )
            result = OrderResult(
                order_id=order_id,
                status=OrderStatus.PENDING,
                position_id=None,
                action=OrderAction.OPEN,
                symbol=request.symbol,
                direction=request.direction,
                requested_lots=request.lots,
                submission=self._current_submission(),
                metadata={
                    'broker_ref': None,
                },
            )
            self._check_order_history_limit()
            self._order_history.append(result)
            return result

        # RESTING (LIMIT / STOP / STOP_LIMIT): async submit via the processor worker
        # thread. Storage is the matching list in the executor (Hybrid pattern shared with
        # sim — the resting-order lists are conceptually identical in both pipelines). The
        # processor's drain_inbox routes the SubmitResponse back via
        # _handle_resting_submit_response so the broker_ref gets confirmed (or the entry
        # removed on rejection / filled on sync-fill).
        #
        # The TYPE is carried through rather than assumed. Both of these lines used to say
        # OrderType.LIMIT, which was invisible while the feature gate above admitted
        # nothing else — and would have put a STOP on the wire as a plain limit while the
        # payload builder's own refusal never fired, because it was handed LIMIT (#500).
        #
        # 1) Append placeholder PendingOrder with broker_ref=None so the
        #    algo's has_pending_orders() blocks during the in-flight window.
        is_stop = request.order_type in (OrderType.STOP, OrderType.STOP_LIMIT)
        pending = PendingOrder(
            pending_order_id=order_id,
            order_action=PendingOrderAction.OPEN,
            order_type=request.order_type,
            timing=PendingOrderTiming(submitted_at=datetime.now(timezone.utc)),
            broker_ref=None,
            symbol=request.symbol,
            direction=request.direction,
            lots=request.lots,
            # For a stop the resting price IS the trigger — same convention as the
            # simulation, whose trigger check reads entry_price.
            entry_price=request.stop_price if is_stop else request.price,
            entry_time=self.get_current_time(),
            order_kwargs=order_kwargs,
            submission=self._current_submission(),
        )
        if is_stop:
            self._active_stop_orders.append(pending)
        else:
            self._active_limit_orders.append(pending)

        # 2) Enqueue the SubmitJob — worker handles HTTP, drain_inbox
        #    routes the response to _handle_resting_submit_response.
        self._request_processor.submit_open_order_async(
            order_id=order_id,
            symbol=request.symbol,
            direction=request.direction,
            lots=request.lots,
            order_type=request.order_type,
            adapter=self.broker.adapter,
            client_order_id=self.build_client_order_id(order_id),
            **order_kwargs,
        )

        # 3) Return PENDING immediately; broker_ref set later by drain
        result = OrderResult(
            order_id=order_id,
            status=OrderStatus.PENDING,
            position_id=None,
            action=OrderAction.OPEN,
            symbol=request.symbol,
            direction=request.direction,
            requested_lots=request.lots,
            submission=self._current_submission(),
            metadata={
                'broker_ref': None,
            },
        )
        self._check_order_history_limit()
        self._order_history.append(result)
        return result

    # ============================================
    # Close Commands (live-specific)
    # ============================================

    def close_position(
        self,
        position_id: str,
        lots: Optional[float] = None,
        close_reason: CloseReason = CloseReason.MANUAL,
    ) -> OrderResult:
        """
        Send close order to broker.

        Args:
            position_id: Position to close
            lots: Lots to close (None = close all)
            close_reason: Why — stored on the pending close and read back at the
                fill, since the two are a round trip apart (#500)

        Returns:
            OrderResult with PENDING or REJECTED status
        """
        # Check position exists in portfolio
        position = self.portfolio.get_position(position_id)
        if not position:
            return create_rejection_result(
                order_id=f'close_{position_id}',
                reason=RejectionReason.BROKER_ERROR,
                message=f'Position {position_id} not found',
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
            submission=self._current_submission(),
            close_reason=close_reason,
        )
        self._request_processor.submit_close_order_async(
            position_id=position_id,
            symbol=position.symbol,
            close_direction=close_direction,
            close_lots=close_lots,
            adapter=self.broker.adapter,
            client_order_id=self.build_client_order_id(position_id),
        )

        return OrderResult(
            order_id=position_id,
            status=OrderStatus.PENDING,
            position_id=None,
            executed_lots=close_lots,
            execution_time=self.get_current_time(),
            action=OrderAction.CLOSE,
            symbol=position.symbol,
            direction=position.direction,
            requested_lots=close_lots,
            submission=self._current_submission(),
            metadata={'awaiting_fill': True, 'broker_ref': None},
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

        # Snap to the symbol's price precision before broker submit + local apply.
        digits = self.broker.get_symbol_specification(position.symbol).digits
        effective_sl = self._round_price(effective_sl, digits)
        effective_tp = self._round_price(effective_tp, digits)

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
            f'✏️ Position {position_id} modify scheduled — '
            f'sl={effective_sl}, tp={effective_tp}'
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
        if target_pending.execution_state.in_flight_operation != PendingOperation.NONE:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.OPERATION_BUSY)

        # Translate UNSET → adapter args (None = no change at the adapter layer)
        adapter_price = None if isinstance(new_price, _UnsetType) else new_price
        adapter_sl = None if isinstance(new_stop_loss, _UnsetType) else new_stop_loss
        adapter_tp = None if isinstance(new_take_profit, _UnsetType) else new_take_profit

        # Snap to the symbol's price precision before broker submit + local apply.
        digits = self.broker.get_symbol_specification(target_pending.symbol).digits
        adapter_price = self._round_price(adapter_price, digits)
        adapter_sl = self._round_price(adapter_sl, digits)
        adapter_tp = self._round_price(adapter_tp, digits)

        # Mark in-flight on the target and store provisional values.
        # The drain handler (_handle_modify_response) consumes these on
        # successful response and applies them to entry_price / order_kwargs.
        target_pending.execution_state.in_flight_operation = PendingOperation.PENDING_MODIFY
        target_pending.execution_state.pending_modification = ModificationRequest(
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
            order_type=target_pending.order_type,
            new_price=adapter_price,
            new_stop_loss=adapter_sl,
            new_take_profit=adapter_tp,
            adapter=self.broker.adapter,
        )

        self.logger.info(
            f'✏️ Limit order {order_id} modify scheduled — '
            f'price={adapter_price}, sl={adapter_sl}, tp={adapter_tp} '
            f'(broker_ref={target_pending.broker_ref})'
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
        doesn't declare stop_orders or stop_limit_orders. A stop can rest in live since
        #500 — from the boot adoption of a venue-reported one, and from a submit once the
        executor declares the type.

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

        if target_pending.execution_state.in_flight_operation != PendingOperation.NONE:
            return ModificationResult(
                success=False,
                rejection_reason=ModificationRejectionReason.OPERATION_BUSY)

        # Translate UNSET → None at adapter boundary
        adapter_stop = None if isinstance(new_stop_price, _UnsetType) else new_stop_price
        adapter_limit = None if isinstance(new_limit_price, _UnsetType) else new_limit_price
        adapter_sl = None if isinstance(new_stop_loss, _UnsetType) else new_stop_loss
        adapter_tp = None if isinstance(new_take_profit, _UnsetType) else new_take_profit

        # Snap to the symbol's price precision before broker submit + local apply.
        digits = self.broker.get_symbol_specification(target_pending.symbol).digits
        adapter_stop = self._round_price(adapter_stop, digits)
        adapter_limit = self._round_price(adapter_limit, digits)
        adapter_sl = self._round_price(adapter_sl, digits)
        adapter_tp = self._round_price(adapter_tp, digits)

        # For a STOP modify, new_price IS the trigger; a STOP_LIMIT's limit price travels
        # beside it as new_limit_price. The EditJob used to carry only new_price, so the
        # limit half was applied to our own order_kwargs and never sent — the venue kept
        # the old limit while our book showed the new one (#500).
        target_pending.execution_state.in_flight_operation = PendingOperation.PENDING_MODIFY
        target_pending.execution_state.pending_modification = ModificationRequest(
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
            order_type=target_pending.order_type,
            new_price=adapter_stop,
            new_limit_price=adapter_limit,
            new_stop_loss=adapter_sl,
            new_take_profit=adapter_tp,
            adapter=self.broker.adapter,
        )

        self.logger.info(
            f'✏️ Stop order {order_id} modify scheduled — '
            f'stop={adapter_stop}, limit={adapter_limit}, '
            f'sl={adapter_sl}, tp={adapter_tp}'
        )

        return ModificationResult(
            success=True,
            status=ModificationStatus.PENDING,
            order_id=order_id,
        )

    def _cancel_resting_order(
        self,
        order_id: str,
        orders: List[PendingOrder],
        label: str,
    ) -> bool:
        """
        Schedule cancellation of one resting order via the async pattern (#318).

        Sets in_flight_operation=PENDING_CANCEL on the target and enqueues a CancelJob to
        the worker thread. The order leaves its list only when the broker's CancelResponse
        arrives via drain_inbox.

        Shared by both resting worlds. It used to exist only for limits, and the stop twin
        was a shorter copy that returned a bare False in the deferred case below — so a stop
        could not be cancelled during its own submit window, which is where every
        cancel-then-close sequence begins.

        Args:
            order_id: Order ID to cancel
            orders: The resting list to search (limit world or stop world)
            label: What to call the order in the log

        Returns:
            True if the cancellation was scheduled or deferred. False if the order is not in
            that list, or is busy with another operation.
        """
        for pending in orders:
            if pending.pending_order_id != order_id:
                continue
            if pending.broker_ref is None:
                # Submit still in-flight: defer the cancel (#361) — park the intent and
                # auto-issue it once _handle_resting_submit_response confirms the broker_ref.
                # Dropping it here orphaned the order (#13/#15 cert blocker, proven live).
                pending.execution_state.cancel_requested = True
                self.logger.debug(
                    f'[CANCEL_DEFER] order={order_id} reason=broker_ref_in_flight')
                return True
            if pending.execution_state.in_flight_operation != PendingOperation.NONE:
                self.logger.debug(
                    f'[CANCEL_SKIP] order={order_id} reason=busy '
                    f'op={pending.execution_state.in_flight_operation.name} scheduled=0')
                return False  # busy

            pending.execution_state.in_flight_operation = PendingOperation.PENDING_CANCEL
            self._request_processor.submit_cancel_order_async(
                order_id=order_id,
                broker_ref=pending.broker_ref,
                adapter=self.broker.adapter,
            )
            self.logger.info(
                f'❌ {label} order {order_id} cancel scheduled '
                f'(broker_ref={pending.broker_ref})'
            )
            return True
        self.logger.debug(
            f'[CANCEL_SKIP] order={order_id} reason=not_in_active_{label.lower()}s scheduled=0')
        return False

    def cancel_limit_order(self, order_id: str) -> bool:
        """
        Schedule cancellation of an active limit order via async pattern (#318).

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancellation was scheduled or deferred. False if order not found or busy.
        """
        return self._cancel_resting_order(order_id, self._active_limit_orders, 'Limit')

    def cancel_stop_order(self, order_id: str) -> bool:
        """
        Schedule cancellation of an active stop order via async pattern (#318).

        NOT capability-gated, deliberately. It used to refuse when the adapter declared no
        stop support — but the only orders in this list are ones we PLACED or ADOPTED, so the
        venue has already accepted the type by the time anyone can ask to cancel one.
        Refusing to cancel an order that exists is how an orphan is made, which is the thing a
        protective order must never become. A capability belongs on the SUBMIT, where it can
        still prevent something.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancellation was scheduled or deferred. False if the order is not resting
            here, or it is busy with another operation.
        """
        return self._cancel_resting_order(order_id, self._active_stop_orders, 'Stop')

    # ============================================
    # Pending Order Awareness
    # ============================================

    def has_pipeline_orders(self) -> bool:
        """Check if any orders are in the broker tracker (MARKET orders in transit)."""
        return self._request_processor.has_pending_orders()

    def get_supported_order_types(self) -> FrozenSet[OrderType]:
        """
        The four types the live path has built end to end (#500).

        STOP and STOP_LIMIT joined MARKET and LIMIT once every layer between the strategy
        and the wire had learned them: the submit carries the requested type instead of
        hardcoding LIMIT, the trigger and limit prices are validated and forwarded, the
        drain routes their submit response so the broker reference gets confirmed, the poll
        loop and the session-end cleanup cover the stop list, and the Kraken builder maps
        them with Kraken's own price semantics. Widening this set BEFORE those layers would
        not have failed loudly: the non-MARKET branch of open_order passed LIMIT to the
        builder, so the builder's refusal could never fire.

        TRAILING_STOP and ICEBERG stay out. Both are venue capabilities Kraken declares and
        neither has a payload mapping or a trigger path here, so the intersection with the
        adapter refuses them at pre-flight and names this side as the short one. #164 / #209.

        Returns:
            The live executor's routable types
        """
        return frozenset({
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT,
        })

    def get_pipeline_orders(self) -> List[PendingOrder]:
        """
        The request processor's unconfirmed orders — in transit, not yet resting or filled.

        Returns:
            The processor's PendingOrders
        """
        return self._request_processor.get_pending_orders()

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
        if pending.timing.submitted_at is None:
            return None
        elapsed = datetime.now(timezone.utc) - pending.timing.submitted_at
        return elapsed.total_seconds() * 1000

    # ============================================
    # Cleanup
    # ============================================

    def finish_remaining_orders(self, cancel_orders: bool = True, current_msc: int = 0) -> None:
        """
        Finish the session's orders — the venue keeps whatever it still holds (#492).

        Three phases:
        1. Resting orders: cancelled AT THE BROKER and expired locally, or left standing
           when the policy says so. Left standing means left in both places — a live order
           must not be recorded as expired while it can still fill.
        2. clear_pending() catches genuine stuck-in-pipeline orders (e.g. the broker has
           not confirmed a fill yet when the session ends). These ARE real anomalies and
           are correctly recorded as FORCE_CLOSED with reason="scenario_end".
        3. The request worker is stopped.

        Open POSITIONS are deliberately untouched. This method used to direct-fill them
        through a synthetic close order that never reached the venue, which reported a
        realised exit nobody executed while the asset sat in the account (#492).

        Args:
            cancel_orders: False leaves resting orders at the venue for a later session to
                adopt (#355)
            current_msc: Not used in live mode (latency is time-based)
        """
        # Phase 1: Resting orders — cancel at the broker, or leave them where they are.
        # BOTH lists. This used to read `_active_limit_orders` alone, on the stated grounds
        # that a stop can never rest in live — and the guard was on that list's truthiness in
        # both branches, so a session holding only stops ran NEITHER: nothing cancelled at
        # the venue, nothing expired locally, not even a log line. A stop can rest here as
        # soon as one is adopted at boot (#500).
        resting = self._active_limit_orders + self._active_stop_orders
        if resting and not cancel_orders:
            self.logger.info(
                f'📋 {len(resting)} active resting order(s) LEFT STANDING '
                f'at the broker by policy — a later session adopts them back (#355). They '
                f'are not expired locally either, because they have not expired.')
        elif resting:
            self.logger.info(
                f'📋 {len(resting)} active resting orders '
                f'at session end — cancelling at broker')
            # The venue's ANSWER decides what we may record. `cancel_order_sync` catches its
            # own transport fault and returns a failure response rather than raising, so the
            # `except` this loop used to rely on could never fire — and `_expire_active_orders`
            # then booked EXPIRED regardless. A venue outage at shutdown left the order working
            # at Kraken while our books called it finished, and the next boot adopted nothing.
            unconfirmed = []
            for pending in resting:
                if not pending.broker_ref:
                    continue
                label = pending.order_type.value if pending.order_type else 'order'
                try:
                    response = self._request_processor.cancel_order_sync(
                        broker_ref=pending.broker_ref,
                        adapter=self.broker.adapter,
                    )
                except Exception as e:                       # noqa: BLE001 — reported below
                    unconfirmed.append((pending, label, str(e)))
                    continue
                if response.status != BrokerOrderStatus.CANCELLED:
                    unconfirmed.append(
                        (pending, label, response.rejection_reason or response.status.value))

            if unconfirmed:
                # §35: the session channel, not the global log — this has to reach the summary.
                for pending, label, reason in unconfirmed:
                    self.logger.error(
                        f'❌ Session end: the venue did NOT confirm the cancel of {label} '
                        f'{pending.pending_order_id} (broker_ref={pending.broker_ref}): '
                        f'{reason}. It may still be working at the broker — it is NOT recorded '
                        f'as expired, and the next session will find it.')
            self._expire_active_orders(
                skip={p.pending_order_id for p, _, _ in unconfirmed})

        # Phase 2: Catch genuine stuck-in-pipeline orders (real anomalies)
        self._request_processor.clear_pending(reason='scenario_end')

        # #318 — clear pending position modifications (live tracker)
        if self._pending_position_modifications:
            self.logger.info(
                f'✏️ {len(self._pending_position_modifications)} pending '
                f'position modifications at scenario end — discarded'
            )
            self._pending_position_modifications.clear()

        # Phase 3: Stop the worker thread cleanly
        self._request_processor.stop_worker()
