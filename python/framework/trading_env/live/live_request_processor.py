# ============================================
# python/framework/trading_env/live/live_request_processor.py
# ============================================
"""
FiniexTestingIDE - Live Request Processor

Owns the full live order lifecycle: submit-orchestration, storage,
fill detection, outcome notification. Live-side counterpart to
OrderLatencySimulator on the simulation side.

Architecture:
    AbstractPendingOrderManager  (storage, query, has_pending, mark_filled/rejected base)
        |
        +-- OrderLatencySimulator      (simulation: in-process seeded delay)
        |
        +-- LiveRequestProcessor       (live: broker-driven, sync in V1, async post-step-6)

Responsibilities:
- High-level orchestrators (submit_open_order / submit_close_order):
  compose adapter Tier-3 layers (build → request → parse), store the
  resulting PendingOrder, return the BrokerResponse to the caller.
- Pure storage (register_pending_open / register_pending_close):
  inherited storage with broker_ref indexing.
- Outcome handling (mark_filled / mark_rejected): remove from pending
  and return the PendingOrder so the caller can fill the portfolio.
- Live-specific lookups (get_by_broker_ref, get_broker_ref,
  update_broker_ref) and timeout detection (check_timeouts).

Modes:
- submit_open_order / submit_close_order: synchronous orchestrator
  (compose Tier-3 layers, block caller for one broker roundtrip)
- submit_open_order_async / submit_close_order_async: enqueue a
  SubmitJob to the worker thread; drain_inbox() picks up the response
  on the next tick and routes it to the executor hooks
- modify_order_sync / cancel_order_sync / query_order_sync: synchronous
  orchestrators over the corresponding Tier-3 operation triples
"""

import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_pending_order_manager import AbstractPendingOrderManager
from python.framework.trading_env.adapters.abstract_adapter import AbstractAdapter
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
    TimeoutConfig,
)
from python.framework.types.live_types.live_request_types import (
    CancelJob,
    CancelResponse,
    EditJob,
    EditResponse,
    PositionModifyJob,
    PositionModifyResponse,
    QueryJob,
    QueryResponse,
    SubmitJob,
    SubmitResponse,
    TradesQueryJob,
    TradesQueryResponse,
)
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderResult,
    OrderType,
    RejectionReason,
    create_rejection_result,
)
from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata


class LiveRequestProcessor(AbstractPendingOrderManager):
    """
    Live-side request processor and pending order manager.

    Combines storage (inherited from AbstractPendingOrderManager) with
    high-level submit orchestration that drives the adapter's Tier-3
    layers. Storage semantics: pending orders indexed by broker_ref
    (O(1) lookup), timeout detection, broker-ref replacement on modify.

    Orchestration: sync orchestrators (submit/query/cancel/modify) block
    the caller on one broker roundtrip; async orchestrators dispatch
    SubmitJob to a daemon worker thread and surface responses on the
    next drain_inbox() call from the main thread.
    """

    def __init__(self, logger: AbstractLogger, timeout_config: TimeoutConfig):
        """
        Initialize the live request processor.

        Args:
            logger: Logger instance
            timeout_config: Timeout thresholds for order monitoring
        """
        super().__init__(logger)
        self._timeout_config = timeout_config

        # Broker ref → order_id index for O(1) lookup when responses arrive
        self._broker_ref_index: Dict[str, str] = {}

        # Worker-thread infrastructure for async dispatch. The worker
        # consumes SubmitJob (and later EditJob/CancelJob — #318) from
        # _http_outbox, performs the broker-side transport, and pushes
        # SubmitResponse objects into _http_inbox. drain_inbox() on the
        # main thread consumes responses and invokes the executor hooks.
        self._http_outbox: queue.Queue = queue.Queue()
        self._http_inbox: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_running: bool = False

        # Executor hooks invoked from drain_inbox() on the main thread.
        # Registered by LiveTradeExecutor in its __init__ via
        # set_executor_hooks(). All hooks run on the main thread.
        # MARKET responses are dispatched inside the processor (fill_open
        # / fill_close / rejection hooks). LIMIT responses are forwarded
        # to limit_response_hook so the executor can update its
        # _active_limit_orders list (Hybrid pattern — shared storage).
        # #318 — modify/cancel/position-modify responses forwarded to
        # their respective hooks since the target order/position lives in
        # the executor's _active_*_orders / portfolio (Hybrid pattern).
        self._fill_open_hook: Optional[Callable[[PendingOrder, float], None]] = None
        self._fill_close_hook: Optional[Callable[[PendingOrder, float], None]] = None
        self._rejection_hook: Optional[Callable[[OrderDirection, OrderResult], None]] = None
        self._limit_response_hook: Optional[Callable[[str, 'BrokerResponse'], None]] = None
        self._modify_response_hook: Optional[Callable[[str, 'BrokerResponse'], None]] = None
        self._cancel_response_hook: Optional[Callable[[str, 'BrokerResponse'], None]] = None
        self._position_modify_response_hook: Optional[Callable[[str, 'BrokerResponse'], None]] = None
        # #326: trades-query drain handler — main-thread callback receiving
        # the parsed TradesQueryResponse so the executor can append trades to
        # pending.trades and trigger _fill_open_order with cumulative truth.
        self._trades_response_hook: Optional[Callable[[TradesQueryResponse], None]] = None
        # #320: status-poll drain handler — main-thread callback receiving the
        # parsed QueryResponse so the executor can clear in_flight_query, run
        # the stale-broker_ref guard, and resolve FILLED / terminal / pending
        # states from the broker's authoritative view.
        self._query_response_hook: Optional[Callable[[QueryResponse], None]] = None

    # ============================================
    # High-Level Orchestrators (sync in V1, async post-step-6)
    # ============================================

    def submit_open_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        order_type: OrderType,
        adapter: AbstractAdapter,
        **kwargs,
    ) -> BrokerResponse:
        """
        Submit a new open order via the adapter (synchronous in V1).

        Composes adapter Tier-3 layers: _build_submit_payload →
        _do_request_submit → _parse_submit_response. Pure orchestrator —
        no storage side-effect. The caller decides whether to register
        the result via register_pending_open (MARKET path) or to track
        it elsewhere (LIMIT → _active_limit_orders, migrated in step 7).

        Args:
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Order size
            order_type: MARKET or LIMIT
            adapter: Live-capable adapter (must implement Tier-3 layers)
            **kwargs: price (LIMIT), stop_loss, take_profit, comment, expected_price

        Returns:
            BrokerResponse from the adapter (REJECTED on transport error)
        """
        payload = adapter._build_submit_payload(
            symbol=symbol,
            direction=direction,
            lots=lots,
            order_type=order_type,
            **kwargs,
        )

        try:
            raw = adapter._do_request_submit(payload)
        except Exception as e:
            return BrokerResponse(
                broker_ref='',
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=datetime.now(timezone.utc),
            )

        return adapter._parse_submit_response(
            raw,
            timestamp=datetime.now(timezone.utc),
        )

    def submit_close_order(
        self,
        symbol: str,
        close_direction: OrderDirection,
        close_lots: float,
        adapter: AbstractAdapter,
        **kwargs,
    ) -> BrokerResponse:
        """
        Submit a close order via the adapter (synchronous in V1).

        A close is a reverse-direction MARKET order. The caller resolves
        the close direction (opposite of the open position direction)
        before calling. Pure orchestrator — no storage side-effect.

        Args:
            symbol: Trading symbol
            close_direction: Reverse of position direction
            close_lots: Lots to close (full or partial)
            adapter: Live-capable adapter
            **kwargs: comment, expected_price, etc.

        Returns:
            BrokerResponse from the adapter (REJECTED on transport error)
        """
        payload = adapter._build_submit_payload(
            symbol=symbol,
            direction=close_direction,
            lots=close_lots,
            order_type=OrderType.MARKET,
            **kwargs,
        )

        try:
            raw = adapter._do_request_submit(payload)
        except Exception as e:
            return BrokerResponse(
                broker_ref='',
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=datetime.now(timezone.utc),
            )

        return adapter._parse_submit_response(
            raw,
            timestamp=datetime.now(timezone.utc),
        )

    # ============================================
    # Pure Storage (broker_ref-indexed)
    # ============================================

    def register_pending_open(
        self,
        order_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        broker_ref: Optional[str],
        order_kwargs: Optional[Dict] = None,
        submission: Optional[SubmissionMetadata] = None,
    ) -> str:
        """
        Track a submitted OPEN order with broker reference.

        Pure storage — the broker call has already happened (via the
        orchestrator) or has been enqueued for async dispatch. The
        broker_ref is optional: if None, the pending is tracked in
        _pending_orders but not indexed yet. drain_inbox() updates the
        broker_ref and the index when the worker confirmation arrives.

        Args:
            order_id: Internal order identifier
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Position size
            broker_ref: Broker's order reference ID, or None during the
                        async submit-in-flight window
            order_kwargs: Optional order parameters (stop_loss, take_profit, comment)
            submission: Submission-moment snapshot for the SLIPPAGE audit
                        channel (#340/#345). None when no tick is in scope
                        (cold-start, heartbeat-only path).

        Returns:
            order_id for chaining
        """
        now = datetime.now(timezone.utc)
        timeout_at = now + timedelta(seconds=self._timeout_config.order_timeout_seconds)

        pending = PendingOrder(
            pending_order_id=order_id,
            order_action=PendingOrderAction.OPEN,
            submitted_at=now,
            broker_ref=broker_ref,
            timeout_at=timeout_at,
            symbol=symbol,
            direction=direction,
            lots=lots,
            entry_time=now,
            order_kwargs=order_kwargs or {},
            submission=submission if submission else SubmissionMetadata(),
        )

        self.store_order(pending)
        if broker_ref is not None:
            self._broker_ref_index[broker_ref] = order_id

        ref_str = broker_ref if broker_ref is not None else 'awaiting confirmation'
        self.logger.info(
            f"Live order tracked: {order_id} (broker_ref={ref_str}) "
            f"timeout_at={timeout_at.isoformat()}"
        )

        return order_id

    def register_pending_close(
        self,
        position_id: str,
        broker_ref: Optional[str],
        close_lots: Optional[float] = None,
        submission: Optional[SubmissionMetadata] = None,
    ) -> str:
        """
        Track a submitted CLOSE order with broker reference.

        Pure storage. The broker_ref is optional — None during the async
        submit-in-flight window, set by drain_inbox() when the worker
        confirms.

        Args:
            position_id: Position to close (used as order_id)
            broker_ref: Broker's order reference ID, or None pre-confirmation
            close_lots: Lots to close (None = close all)
            submission: Submission-moment snapshot for the SLIPPAGE audit
                        channel (#340/#345). Each partial close captures its
                        own submission tick.

        Returns:
            position_id for chaining
        """
        now = datetime.now(timezone.utc)
        timeout_at = now + timedelta(seconds=self._timeout_config.order_timeout_seconds)

        pending = PendingOrder(
            pending_order_id=position_id,
            order_action=PendingOrderAction.CLOSE,
            submitted_at=now,
            broker_ref=broker_ref,
            timeout_at=timeout_at,
            close_lots=close_lots,
            submission=submission if submission else SubmissionMetadata(),
        )

        self.store_order(pending)
        if broker_ref is not None:
            self._broker_ref_index[broker_ref] = position_id

        ref_str = broker_ref if broker_ref is not None else 'awaiting confirmation'
        self.logger.info(
            f"Live close order tracked: {position_id} (broker_ref={ref_str})"
        )

        return position_id

    # ============================================
    # Outcome Handling
    # ============================================

    def mark_filled(
        self,
        broker_ref: str,
        fill_price: float,
        filled_lots: float,
    ) -> Optional[PendingOrder]:
        """
        Mark order as filled by broker. Removes from pending.

        Returns the PendingOrder so the executor can call inherited
        _fill_open_order() / _fill_close_order().

        Args:
            broker_ref: Broker's order reference ID
            fill_price: Broker's execution price
            filled_lots: Actual filled volume

        Returns:
            PendingOrder if found, None if broker_ref unknown
        """
        order_id = self._broker_ref_index.pop(broker_ref, None)
        if order_id is None:
            self.logger.warning(
                f"mark_filled: unknown broker_ref={broker_ref}"
            )
            return None

        pending = self.remove_order(order_id)
        if pending is None:
            self.logger.warning(
                f"mark_filled: order_id={order_id} not in pending cache"
            )
            return None

        price_str = f"{fill_price:.5f}" if fill_price is not None else 'N/A'
        self.logger.info(
            f"Order filled: {order_id} at {price_str} "
            f"({filled_lots} lots, broker_ref={broker_ref})"
        )

        # Dry-run market orders have no real fill price — Kraken validate
        # mode returns no execution data. Replaced by DryRunOrderSimulator
        # in a later refactor step.
        if fill_price is not None and fill_price == 0.0:
            self.logger.warning(
                f"⚠️  Fill price is 0.00000 for {order_id} — "
                f"dry-run mode cannot determine market fill price. "
                f"P&L calculations will be inaccurate."
            )

        return pending

    def mark_rejected(
        self,
        broker_ref: str,
        reason: str,
    ) -> Optional[PendingOrder]:
        """
        Mark order as rejected by broker. Removes from pending.

        Returns the PendingOrder so the executor can record the
        rejection in _order_history.

        Args:
            broker_ref: Broker's order reference ID
            reason: Broker's rejection reason

        Returns:
            PendingOrder if found, None if broker_ref unknown
        """
        order_id = self._broker_ref_index.pop(broker_ref, None)
        if order_id is None:
            self.logger.warning(
                f"mark_rejected: unknown broker_ref={broker_ref}"
            )
            return None

        pending = self.remove_order(order_id)
        if pending is None:
            self.logger.warning(
                f"mark_rejected: order_id={order_id} not in pending cache"
            )
            return None

        self.logger.warning(
            f"Order rejected: {order_id} reason={reason} "
            f"(broker_ref={broker_ref})"
        )

        return pending

    # ============================================
    # Timeout Detection
    # ============================================

    def check_timeouts(self) -> List[PendingOrder]:
        """
        Return orders that have exceeded their timeout threshold.

        Does NOT remove them from pending — caller decides how to handle
        (retry, cancel, escalate).

        Returns:
            List of PendingOrder objects past timeout_at
        """
        now = datetime.now(timezone.utc)
        timed_out = []

        for pending in self._pending_orders.values():
            if pending.timeout_at and pending.timeout_at <= now:
                timed_out.append(pending)

        return timed_out

    # ============================================
    # Broker Reference Update
    # ============================================

    def update_broker_ref(self, old_ref: str, new_ref: str) -> bool:
        """
        Update broker_ref after broker-side order replacement.

        Some brokers (Kraken EditOrder) return a new txid that replaces
        the original on modification. The caller is responsible for
        invoking this when such a swap occurs.

        Args:
            old_ref: Previous broker reference (now invalid)
            new_ref: New broker reference from broker

        Returns:
            True if old_ref was found and updated, False otherwise
        """
        order_id = self._broker_ref_index.pop(old_ref, None)
        if order_id is None:
            self.logger.warning(
                f"update_broker_ref: old_ref={old_ref} not found in index"
            )
            return False

        self._broker_ref_index[new_ref] = order_id

        pending = self._pending_orders.get(order_id)
        if pending is not None:
            pending.broker_ref = new_ref

        self.logger.info(
            f"Broker ref updated: {order_id} ({old_ref} → {new_ref})"
        )
        return True

    # ============================================
    # Broker Reference Lookup
    # ============================================

    def get_by_broker_ref(self, broker_ref: str) -> Optional[PendingOrder]:
        """
        Look up pending order by broker reference. O(1) via index.

        Args:
            broker_ref: Broker's order reference ID

        Returns:
            PendingOrder if found, None otherwise
        """
        order_id = self._broker_ref_index.get(broker_ref)
        if order_id is None:
            return None
        return self._pending_orders.get(order_id)

    def get_broker_ref(self, order_id: str) -> Optional[str]:
        """
        Reverse lookup: get broker reference for a given order ID.

        O(n) scan of broker_ref index. Used by modify_limit_order paths
        to resolve internal order_id to broker_ref for an adapter call.

        Args:
            order_id: Internal order identifier

        Returns:
            Broker reference string if found, None otherwise
        """
        for broker_ref, mapped_order_id in self._broker_ref_index.items():
            if mapped_order_id == order_id:
                return broker_ref
        return None

    # ============================================
    # Cleanup (override to also clear index)
    # ============================================

    def clear_pending(
        self,
        current_msc: Optional[int] = None,
        reason: str = 'scenario_end',
    ) -> None:
        """Clear all pending orders and the broker_ref index."""
        super().clear_pending(current_msc=current_msc, reason=reason)
        self._broker_ref_index.clear()

    # ============================================
    # Worker Thread — Async Dispatch Infrastructure
    # ============================================
    #
    # Async submit dispatches SubmitJob objects via _http_outbox to the
    # worker thread, which performs the broker-side transport (build →
    # request → parse) and pushes SubmitResponse objects to _http_inbox.
    # drain_inbox() on the main thread consumes the responses and invokes
    # the registered executor hooks for portfolio updates and listener
    # notifications. EditJob / CancelJob support is added in #318.
    # ============================================

    def set_executor_hooks(
        self,
        fill_open: Callable[[PendingOrder, float], None],
        fill_close: Callable[[PendingOrder, float], None],
        on_rejection: Callable[[OrderDirection, OrderResult], None],
        limit_response: Optional[Callable[[str, BrokerResponse], None]] = None,
        modify_response: Optional[Callable[[str, BrokerResponse], None]] = None,
        cancel_response: Optional[Callable[[str, BrokerResponse], None]] = None,
        position_modify_response: Optional[Callable[[str, BrokerResponse], None]] = None,
        trades_response: Optional[Callable[[TradesQueryResponse], None]] = None,
        query_response: Optional[Callable[[QueryResponse], None]] = None,
    ) -> None:
        """
        Register executor callbacks for async outcomes.

        Invoked by LiveTradeExecutor in its __init__. Hooks are called from
        the main thread inside drain_inbox(), never from the worker thread,
        so they may safely mutate portfolio / order_history / listener state
        without locking.

        Args:
            fill_open: _fill_open_order(pending, fill_price) — handles
                       MARKET OPEN fills (portfolio add, history append)
            fill_close: _fill_close_order(pending, fill_price) — handles
                        MARKET CLOSE fills (portfolio close, history append)
            on_rejection: _record_async_rejection(direction, OrderResult) —
                          handles MARKET broker-side rejection (counter,
                          history, listener notification)
            limit_response: Optional — _handle_limit_submit_response(order_id,
                            broker_response). Invoked for LIMIT submit
                            responses so the executor can update its
                            _active_limit_orders list (Hybrid pattern).
            modify_response: Optional (#318) — _handle_modify_response(order_id,
                            broker_response). Invoked for EditResponse so the
                            executor can apply the modification to the target
                            in _active_limit_orders / _active_stop_orders and
                            clear in_flight_operation.
            cancel_response: Optional (#318) — _handle_cancel_response(order_id,
                            broker_response). Invoked for CancelResponse so the
                            executor can remove the cancelled order from its
                            active list (or handle race-with-fill on rejection).
            position_modify_response: Optional (#318) —
                            _handle_position_modify_response(position_id,
                            broker_response). Invoked for PositionModifyResponse
                            so the executor can apply SL/TP to portfolio.
            trades_response: Optional (#326) — _handle_trades_response(response).
                            Invoked for TradesQueryResponse so the executor can
                            append per-execution BrokerTrade records to the
                            parent PendingOrder and finalize the fill with
                            cumulative truth (price + fee).
            query_response: Optional (#320) — _handle_query_response(response).
                            Invoked for QueryResponse so the executor can clear
                            in_flight_query on the polled PendingOrder, apply
                            the stale-broker_ref guard, and react to FILLED /
                            terminal / pending broker outcomes.
        """
        self._fill_open_hook = fill_open
        self._fill_close_hook = fill_close
        self._rejection_hook = on_rejection
        self._limit_response_hook = limit_response
        self._modify_response_hook = modify_response
        self._cancel_response_hook = cancel_response
        self._position_modify_response_hook = position_modify_response
        self._trades_response_hook = trades_response
        self._query_response_hook = query_response

    def start_worker(self) -> None:
        """
        Start the daemon worker thread. Idempotent — safe to call more than once.
        """
        if self._worker_running:
            return
        self._worker_running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f'LiveRequestProcessor-Worker-{id(self):x}',
        )
        self._worker_thread.start()
        self.logger.info(f'Worker thread started: {self._worker_thread.name}')

    def stop_worker(self, timeout: float = 2.0) -> None:
        """
        Stop the worker thread cleanly. Safe to call when not running.

        Args:
            timeout: Max seconds to wait for the worker loop to exit
        """
        if not self._worker_running:
            return
        self._worker_running = False
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout)
            if self._worker_thread.is_alive():
                self.logger.warning(
                    f'Worker thread did not shut down within {timeout}s — '
                    f'continuing (daemon)'
                )
            else:
                self.logger.info('Worker thread stopped cleanly')
        self._worker_thread = None

    def _worker_loop(self) -> None:
        """
        Daemon worker loop. Pulls jobs from _http_outbox and dispatches them.

        Job types (#318 + #319):
        - SubmitJob: submit a new order (open or close)
        - EditJob: modify a pending order
        - CancelJob: cancel a pending order
        - PositionModifyJob: modify an open position's SL/TP

        The short get() timeout keeps shutdown latency bounded (max one
        timeout interval after stop_worker).
        """
        while self._worker_running:
            try:
                job = self._http_outbox.get(timeout=0.5)
            except queue.Empty:
                continue

            if isinstance(job, SubmitJob):
                self._dispatch_submit_job(job)
            elif isinstance(job, EditJob):
                self._dispatch_edit_job(job)
            elif isinstance(job, CancelJob):
                self._dispatch_cancel_job(job)
            elif isinstance(job, PositionModifyJob):
                self._dispatch_position_modify_job(job)
            elif isinstance(job, TradesQueryJob):
                self._dispatch_trades_query_job(job)
            elif isinstance(job, QueryJob):
                self._dispatch_query_job(job)
            else:
                self.logger.warning(
                    f'Unknown job type in outbox: {type(job).__name__}'
                )

    def _dispatch_submit_job(self, job: SubmitJob) -> None:
        """
        Worker-thread handler for SubmitJob.

        Performs the broker transport via the adapter's Tier-3 layers,
        wraps the result in a SubmitResponse, and pushes it to the inbox.
        Transport errors are caught and surfaced as REJECTED responses.

        Args:
            job: The SubmitJob enqueued by submit_open_order_async or
                 submit_close_order_async
        """
        now = datetime.now(timezone.utc)
        try:
            raw = job.adapter._do_request_submit(job.payload)
            response = job.adapter._parse_submit_response(raw, timestamp=now)
        except Exception as e:
            response = BrokerResponse(
                broker_ref='',
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )

        self._http_inbox.put(SubmitResponse(
            order_id=job.order_id,
            action=job.action,
            order_type=job.order_type,
            broker_response=response,
        ))

    def _dispatch_edit_job(self, job: EditJob) -> None:
        """
        Worker-thread handler for EditJob (#318).

        Composes adapter Tier-3 modify layer:
            _build_modify_payload → _do_request_modify → _parse_modify_response

        Transport errors are surfaced as REJECTED BrokerResponse. The main
        thread's drain_inbox routes the EditResponse to the executor via
        _modify_response_hook for state application.

        Args:
            job: EditJob enqueued by submit_modify_order_async
        """
        now = datetime.now(timezone.utc)
        try:
            payload = job.adapter._build_modify_payload(
                broker_ref=job.broker_ref,
                symbol=job.symbol,
                new_price=job.new_price,
                new_stop_loss=job.new_stop_loss,
                new_take_profit=job.new_take_profit,
            )
            raw = job.adapter._do_request_modify(payload)
            response = job.adapter._parse_modify_response(
                raw, original_broker_ref=job.broker_ref, timestamp=now,
            )
        except Exception as e:
            response = BrokerResponse(
                broker_ref=job.broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )

        self._http_inbox.put(EditResponse(
            order_id=job.order_id,
            broker_response=response,
        ))

    def _dispatch_cancel_job(self, job: CancelJob) -> None:
        """
        Worker-thread handler for CancelJob (#318).

        Composes adapter Tier-3 cancel layer:
            _build_cancel_payload → _do_request_cancel → _parse_cancel_response
        """
        now = datetime.now(timezone.utc)
        try:
            payload = job.adapter._build_cancel_payload(job.broker_ref)
            raw = job.adapter._do_request_cancel(payload)
            response = job.adapter._parse_cancel_response(
                raw, job.broker_ref, now,
            )
        except Exception as e:
            response = BrokerResponse(
                broker_ref=job.broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )

        self._http_inbox.put(CancelResponse(
            order_id=job.order_id,
            broker_response=response,
        ))

    def _dispatch_position_modify_job(self, job: PositionModifyJob) -> None:
        """
        Worker-thread handler for PositionModifyJob (#318).

        Position-modify Tier-3 surface is adapter-specific. The current
        contract assumes the adapter exposes
            _build_position_modify_payload(position_id, symbol, new_sl, new_tp)
            _do_request_position_modify(payload)
            _parse_position_modify_response(raw, position_id, timestamp)
        as a separate operation triple. Adapters that fold position-modify
        into the existing modify operation (#209 design decision) should
        override this dispatch method or expose the same triple names as
        thin aliases.
        """
        now = datetime.now(timezone.utc)
        try:
            payload = job.adapter._build_position_modify_payload(
                position_id=job.position_id,
                symbol=job.symbol,
                new_stop_loss=job.new_stop_loss,
                new_take_profit=job.new_take_profit,
            )
            raw = job.adapter._do_request_position_modify(payload)
            response = job.adapter._parse_position_modify_response(
                raw, position_id=job.position_id, timestamp=now,
            )
        except Exception as e:
            response = BrokerResponse(
                broker_ref=job.position_id,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )

        self._http_inbox.put(PositionModifyResponse(
            position_id=job.position_id,
            broker_response=response,
        ))

    def _dispatch_query_job(self, job: QueryJob) -> None:
        """
        Worker-thread handler for QueryJob (#320).

        Composes adapter Tier-3 query layer:
            _build_query_payload → _do_request_query → _parse_query_response

        Transport errors are surfaced as a REJECTED BrokerResponse — the main
        thread's drain handler clears in_flight_query regardless and decides
        whether the order should be removed (rejected) or simply re-polled on
        the next throttle cycle.
        """
        now = datetime.now(timezone.utc)
        try:
            payload = job.adapter._build_query_payload(job.broker_ref)
            raw = job.adapter._do_request_query(payload)
            response = job.adapter._parse_query_response(raw, job.broker_ref, now)
        except Exception as e:
            response = BrokerResponse(
                broker_ref=job.broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )

        self._http_inbox.put(QueryResponse(
            order_id=job.order_id,
            broker_response=response,
        ))

    def _dispatch_trades_query_job(self, job: TradesQueryJob) -> None:
        """
        Worker-thread handler for TradesQueryJob (#326).

        Composes adapter Tier-3 trades-query layer:
            _build_trades_query_payload → _do_request_trades_query
            → _parse_trades_query_response

        Transport errors are surfaced as a TradesQueryResponse with success=False
        and an empty trades list — the main thread's drain handler decides how
        to react (typically: leave the pending in place, retry on next poll).
        """
        try:
            payload = job.adapter._build_trades_query_payload(job.broker_ref)
            raw = job.adapter._do_request_trades_query(payload)
            trades = job.adapter._parse_trades_query_response(
                raw, broker_ref=job.broker_ref, order_id=job.order_id,
            )
            self._http_inbox.put(TradesQueryResponse(
                order_id=job.order_id,
                broker_ref=job.broker_ref,
                trades=trades,
                success=True,
            ))
        except Exception as e:
            self._http_inbox.put(TradesQueryResponse(
                order_id=job.order_id,
                broker_ref=job.broker_ref,
                trades=[],
                success=False,
                error_message=str(e),
            ))

    def flush_outbox(self, timeout: float = 2.0) -> bool:
        """
        Block until the worker has processed all queued outbox jobs.

        Test helper — production code does not need this since the worker
        runs asynchronously and drain_inbox() picks up responses per tick.
        Tests that need to assert outcomes immediately after a submit
        call should call flush_outbox() to ensure the worker has finished
        before continuing.

        Args:
            timeout: Maximum seconds to wait

        Returns:
            True if outbox drained within timeout, False otherwise
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._http_outbox.empty():
                # Give the worker a brief moment to push to inbox after
                # its last get(): the worker's _dispatch_submit_job call
                # may still be in flight at this point.
                time.sleep(0.02)
                return True
            time.sleep(0.005)
        self.logger.warning(
            f"flush_outbox: outbox not drained within {timeout}s"
        )
        return False

    def drain_inbox(self) -> None:
        """
        Process all pending worker responses. Called from the main thread per tick.

        All portfolio mutations, order_history appends, and listener
        notifications triggered by worker results happen here. Runs O(N)
        where N = number of responses queued since last drain (typically
        0-2 per tick at normal submit rates).
        """
        while True:
            try:
                item = self._http_inbox.get_nowait()
            except queue.Empty:
                break

            if isinstance(item, SubmitResponse):
                self._handle_submit_response(item)
            elif isinstance(item, EditResponse):
                self._handle_edit_response(item)
            elif isinstance(item, CancelResponse):
                self._handle_cancel_response(item)
            elif isinstance(item, PositionModifyResponse):
                self._handle_position_modify_response(item)
            elif isinstance(item, TradesQueryResponse):
                self._handle_trades_query_response(item)
            elif isinstance(item, QueryResponse):
                self._handle_query_response(item)
            else:
                self.logger.warning(
                    f'Unknown response type in inbox: {type(item).__name__}'
                )

    def _handle_submit_response(self, item: SubmitResponse) -> None:
        """
        Main-thread handler for a SubmitResponse from the worker.

        LIMIT responses are delegated to the executor via limit_response_hook
        because LIMIT pendings live in AbstractTradeExecutor._active_limit_orders
        (shared sim/live storage per Hybrid pattern), not in the processor.

        MARKET responses are dispatched inside the processor with three branches:
          REJECTED → remove local pending + invoke rejection hook
          FILLED   → confirm broker_ref + mark_filled + invoke fill hook
          PENDING  → confirm broker_ref (polling takes over)

        Args:
            item: The SubmitResponse delivered via _http_inbox
        """
        # LIMIT path: executor owns the storage in _active_limit_orders
        if item.order_type == OrderType.LIMIT:
            if self._limit_response_hook is not None:
                self._limit_response_hook(item.order_id, item.broker_response)
            else:
                self.logger.warning(
                    f"drain_inbox: LIMIT SubmitResponse for {item.order_id} "
                    f"but no limit_response_hook registered"
                )
            return

        # MARKET path: processor owns the storage in _pending_orders
        pending = self._pending_orders.get(item.order_id)
        if pending is None:
            self.logger.warning(
                f"drain_inbox: SubmitResponse for unknown order_id {item.order_id}"
            )
            return

        response = item.broker_response

        if response.is_rejected:
            # Submit failed at broker — discard local pending, notify executor
            self.remove_order(item.order_id)
            if pending.broker_ref is not None:
                self._broker_ref_index.pop(pending.broker_ref, None)

            if self._rejection_hook is not None:
                rejection = create_rejection_result(
                    order_id=item.order_id,
                    reason=RejectionReason.BROKER_ERROR,
                    message=f"Broker rejected: {response.rejection_reason or 'unknown'}",
                )
                self._rejection_hook(pending.direction, rejection)
            return

        # Non-rejected: confirm broker_ref and update index
        pending.broker_ref = response.broker_ref
        if response.broker_ref:
            self._broker_ref_index[response.broker_ref] = item.order_id

        if response.is_filled:
            # Synchronous-fill broker (mock INSTANT_FILL, Kraken close-on-submit)
            filled = self.mark_filled(
                broker_ref=response.broker_ref,
                fill_price=response.fill_price,
                filled_lots=response.filled_lots,
            )
            if filled is None:
                return
            if item.action == PendingOrderAction.OPEN:
                if self._fill_open_hook is not None:
                    self._fill_open_hook(filled, response.fill_price)
            elif item.action == PendingOrderAction.CLOSE:
                if self._fill_close_hook is not None:
                    self._fill_close_hook(filled, response.fill_price)
        # else: PENDING — pending stays in store, polling will handle it

    # ============================================
    # Async Modify / Cancel / Position-Modify Drain Handlers (#318)
    # ============================================

    def _handle_edit_response(self, item: EditResponse) -> None:
        """
        Main-thread handler for an EditResponse from the worker.

        Delegates to the executor's _modify_response_hook because the target
        PendingOrder lives in the executor's _active_*_orders (Hybrid pattern).
        The hook is responsible for:
          - Applying the modification (entry_price, SL, TP) on success
          - Discarding the provisional pending_modification on rejection
          - Clearing in_flight_operation
          - Calling update_broker_ref(old, new) if the broker returned a new ref
        """
        if self._modify_response_hook is not None:
            self._modify_response_hook(item.order_id, item.broker_response)
        else:
            self.logger.warning(
                f"drain_inbox: EditResponse for {item.order_id} "
                f"but no modify_response_hook registered"
            )

    def _handle_cancel_response(self, item: CancelResponse) -> None:
        """
        Main-thread handler for a CancelResponse.

        Delegates to the executor's _cancel_response_hook. The hook removes
        the order from its active list on success, or surfaces the race
        condition (cancel-during-fill) on rejection.
        """
        if self._cancel_response_hook is not None:
            self._cancel_response_hook(item.order_id, item.broker_response)
        else:
            self.logger.warning(
                f"drain_inbox: CancelResponse for {item.order_id} "
                f"but no cancel_response_hook registered"
            )

    def _handle_position_modify_response(self, item: PositionModifyResponse) -> None:
        """
        Main-thread handler for a PositionModifyResponse.

        Delegates to the executor's _position_modify_response_hook. The hook
        applies SL/TP changes to portfolio.modify_position on success, or
        discards the provisional state on rejection.
        """
        if self._position_modify_response_hook is not None:
            self._position_modify_response_hook(
                item.position_id, item.broker_response,
            )
        else:
            self.logger.warning(
                f"drain_inbox: PositionModifyResponse for {item.position_id} "
                f"but no position_modify_response_hook registered"
            )

    def _handle_query_response(self, item: QueryResponse) -> None:
        """
        Main-thread handler for a QueryResponse (#320).

        Delegates to the executor's _query_response_hook. The hook ALWAYS
        clears pending.in_flight_query (the dispatched query is resolved
        either way), then applies the stale-broker_ref guard before any
        state mutation. Branches on broker status: FILLED → fill, terminal
        → rejection, PENDING/PARTIALLY_FILLED → no state change.
        """
        if self._query_response_hook is not None:
            self._query_response_hook(item)
        else:
            self.logger.warning(
                f"drain_inbox: QueryResponse for {item.order_id} "
                f"but no query_response_hook registered"
            )

    def _handle_trades_query_response(self, item: TradesQueryResponse) -> None:
        """
        Main-thread handler for a TradesQueryResponse (#326).

        Delegates to the executor's _trades_response_hook. The hook appends
        each BrokerTrade to the parent PendingOrder.trades, updates
        cumulative_* aggregates, and finalizes the fill via _fill_open_order.
        See ISSUE_326 §8 Post-Drain Distribution Flow for the full chain.
        """
        if self._trades_response_hook is not None:
            self._trades_response_hook(item)
        else:
            self.logger.warning(
                f"drain_inbox: TradesQueryResponse for {item.order_id} "
                f"but no trades_response_hook registered"
            )

    # ============================================
    # Async Submit Orchestrators (enqueue to worker)
    # ============================================

    def submit_open_order_async(
        self,
        order_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        order_type: OrderType,
        adapter: AbstractAdapter,
        **kwargs,
    ) -> None:
        """
        Async variant of submit_open_order — enqueues a SubmitJob.

        For MARKET orders, the caller MUST first call register_pending_open
        (broker_ref=None) so the order is tracked in the processor's
        _pending_orders during the submit-in-flight window.

        For LIMIT orders, the caller (LiveTradeExecutor) must instead append
        a PendingOrder to its _active_limit_orders list (broker_ref=None).
        drain_inbox routes the LIMIT response to the executor's
        _limit_response_hook for that storage to be updated.

        Args:
            order_id: Internal order identifier
            symbol, direction, lots, order_type: Order parameters
            adapter: Live-capable adapter
            **kwargs: price, stop_loss, take_profit, comment, expected_price
        """
        payload = adapter._build_submit_payload(
            symbol=symbol,
            direction=direction,
            lots=lots,
            order_type=order_type,
            **kwargs,
        )
        self._http_outbox.put(SubmitJob(
            order_id=order_id,
            action=PendingOrderAction.OPEN,
            order_type=order_type,
            payload=payload,
            adapter=adapter,
        ))

    def submit_close_order_async(
        self,
        position_id: str,
        symbol: str,
        close_direction: OrderDirection,
        close_lots: float,
        adapter: AbstractAdapter,
        **kwargs,
    ) -> None:
        """
        Async variant of submit_close_order — enqueues a SubmitJob.

        Close is always a MARKET order. The caller MUST first call
        register_pending_close(broker_ref=None) so the close order is
        tracked during the submit-in-flight window.

        Args:
            position_id: Position being closed (used as order_id)
            symbol: Trading symbol
            close_direction: Reverse of position direction
            close_lots: Lots to close
            adapter: Live-capable adapter
            **kwargs: comment, expected_price, etc.
        """
        payload = adapter._build_submit_payload(
            symbol=symbol,
            direction=close_direction,
            lots=close_lots,
            order_type=OrderType.MARKET,
            **kwargs,
        )
        self._http_outbox.put(SubmitJob(
            order_id=position_id,
            action=PendingOrderAction.CLOSE,
            order_type=OrderType.MARKET,
            payload=payload,
            adapter=adapter,
        ))

    # ============================================
    # Async Modify / Cancel / Position-Modify Orchestrators (#318)
    # ============================================
    #
    # These methods enqueue jobs to the worker thread for non-blocking
    # broker roundtrips. The caller (LiveTradeExecutor) is responsible
    # for setting in_flight_operation on the target PendingOrder before
    # enqueueing — so the busy-check and Algo's discipline pattern work
    # against the visible state. drain_inbox routes responses to the
    # executor via the registered hooks.

    def submit_modify_order_async(
        self,
        order_id: str,
        broker_ref: str,
        symbol: str,
        new_price: Optional[float],
        new_stop_loss: Optional[float],
        new_take_profit: Optional[float],
        adapter: AbstractAdapter,
    ) -> None:
        """
        Enqueue an EditJob for the worker thread.

        Caller (LiveTradeExecutor.modify_limit_order / modify_stop_order) MUST
        already have:
          1. Resolved order_id → broker_ref via _active_*_orders lookup
          2. Set pending.in_flight_operation = PENDING_MODIFY on the target
          3. Stored pending.pending_modification = ModificationRequest(...)
          4. Validated that the order is not busy and not unconfirmed

        The worker dispatches the modify via adapter Tier-3 layers; the
        response arrives via _http_inbox and routes to _modify_response_hook
        on the next drain_inbox.

        Args:
            order_id: Internal order id (matches in_flight target)
            broker_ref: Current broker ref (some brokers return a NEW ref;
                        the hook handles update_broker_ref)
            symbol: Trading symbol (required by some adapters on modify)
            new_price, new_stop_loss, new_take_profit: New values (None = no change)
            adapter: Live-capable adapter
        """
        self._http_outbox.put(EditJob(
            order_id=order_id,
            broker_ref=broker_ref,
            symbol=symbol,
            new_price=new_price,
            new_stop_loss=new_stop_loss,
            new_take_profit=new_take_profit,
            adapter=adapter,
        ))

    def submit_cancel_order_async(
        self,
        order_id: str,
        broker_ref: str,
        adapter: AbstractAdapter,
    ) -> None:
        """
        Enqueue a CancelJob for the worker thread.

        Caller MUST set in_flight_operation = PENDING_CANCEL on the target
        PendingOrder before enqueueing.

        Args:
            order_id: Internal order id
            broker_ref: Current broker ref
            adapter: Live-capable adapter
        """
        self._http_outbox.put(CancelJob(
            order_id=order_id,
            broker_ref=broker_ref,
            adapter=adapter,
        ))

    def submit_modify_position_async(
        self,
        position_id: str,
        symbol: str,
        new_stop_loss: Optional[float],
        new_take_profit: Optional[float],
        adapter: AbstractAdapter,
    ) -> None:
        """
        Enqueue a PositionModifyJob for the worker thread.

        Used by LiveTradeExecutor.modify_position when the adapter declares
        OrderCapabilities.native_position_sl_tp = True (e.g. MT5 in #209).
        For adapters with native_position_sl_tp = False, the executor falls
        back to portfolio.modify_position directly and never enqueues this.

        Caller MUST track the in-flight state separately on the executor
        side (positions don't have a PendingOrder.in_flight_operation flag
        since positions aren't PendingOrders).

        Args:
            position_id: Position identifier
            symbol: Trading symbol
            new_stop_loss, new_take_profit: New SL/TP values
            adapter: Live-capable adapter with native_position_sl_tp = True
        """
        self._http_outbox.put(PositionModifyJob(
            position_id=position_id,
            symbol=symbol,
            new_stop_loss=new_stop_loss,
            new_take_profit=new_take_profit,
            adapter=adapter,
        ))

    def submit_query_order_async(
        self,
        order_id: str,
        broker_ref: str,
        adapter: AbstractAdapter,
    ) -> None:
        """
        Enqueue a QueryJob for the worker thread (#320).

        Triggered by LiveTradeExecutor._process_active_orders for every active
        LIMIT order whose throttle window has elapsed and whose in_flight_query
        is False. The caller is responsible for setting in_flight_query = True
        and updating last_polled_at_ms BEFORE invoking this method — that keeps
        the scheduler state visible to subsequent throttle checks even before
        the job hits the worker.

        Args:
            order_id: Internal order identifier (primary routing key)
            broker_ref: Broker order reference at dispatch time. May be stale
                        by response time (Kraken EditOrder flips refs) — the
                        executor's _handle_query_response applies the
                        stale-broker_ref guard.
            adapter: Live-capable adapter
        """
        self._http_outbox.put(QueryJob(
            order_id=order_id,
            broker_ref=broker_ref,
            adapter=adapter,
        ))

    def submit_trades_query_async(
        self,
        order_id: str,
        broker_ref: str,
        adapter: AbstractAdapter,
    ) -> None:
        """
        Enqueue a TradesQueryJob for the worker thread (#326).

        Triggered by LiveTradeExecutor after a query response detects FILLED.
        The worker fetches per-execution trade records via the adapter's
        Tier-3 trades_query layer; drain_inbox routes the response to
        _handle_trades_response, which appends BrokerTrade records to
        pending.trades and finalizes the fill.

        Args:
            order_id: Internal order identifier (primary routing key)
            broker_ref: Parent order's broker reference
            adapter: Live-capable adapter with trade_level_reporting capability
        """
        self._http_outbox.put(TradesQueryJob(
            order_id=order_id,
            broker_ref=broker_ref,
            adapter=adapter,
        ))

    # ============================================
    # Sync Orchestrators (Tier-3-decoupled — used by Executor for modify/cancel)
    # ============================================

    def modify_order_sync(
        self,
        broker_ref: str,
        symbol: str,
        new_price: Optional[float],
        new_stop_loss: Optional[float],
        new_take_profit: Optional[float],
        adapter: AbstractAdapter,
    ) -> BrokerResponse:
        """
        Synchronously modify an order via the adapter's Tier-3 layers.

        Composes adapter._build_modify_payload → _do_request_modify →
        _parse_modify_response. Blocks the caller thread for one broker
        roundtrip. Async modify (queued through the worker) is the subject
        of #318 and uses the same Tier-3 surface.

        The executor calls this so the Tier-3 layer-composition for modify
        stays encapsulated in the processor (parallel to submit / cancel).

        Args:
            broker_ref: Current broker order reference
            symbol: Trading symbol (some brokers require it on modify)
            new_price: New limit price (None=no change)
            new_stop_loss: New stop loss (None=no change)
            new_take_profit: New take profit (None=no change)
            adapter: Live-capable adapter

        Returns:
            BrokerResponse (REJECTED on transport error, PENDING with new
            broker_ref on success — Kraken EditOrder returns a fresh txid)
        """
        now = datetime.now(timezone.utc)
        payload = adapter._build_modify_payload(
            broker_ref=broker_ref,
            symbol=symbol,
            new_price=new_price,
            new_stop_loss=new_stop_loss,
            new_take_profit=new_take_profit,
        )
        try:
            raw = adapter._do_request_modify(payload)
        except Exception as e:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )
        return adapter._parse_modify_response(raw, original_broker_ref=broker_ref, timestamp=now)

    def query_order_sync(
        self,
        broker_ref: str,
        adapter: AbstractAdapter,
    ) -> BrokerResponse:
        """
        Synchronously query an order's current status via the adapter's Tier-3 layers.

        Composes adapter._build_query_payload → _do_request_query →
        _parse_query_response. Blocks the caller thread for one broker
        roundtrip. Used by LiveTradeExecutor for the Phase-1 / Phase-2
        polling passes inside _process_pending_orders.

        Args:
            broker_ref: Broker order reference to query
            adapter: Live-capable adapter

        Returns:
            BrokerResponse with current status (REJECTED on transport error)
        """
        now = datetime.now(timezone.utc)
        payload = adapter._build_query_payload(broker_ref)
        try:
            raw = adapter._do_request_query(payload)
        except Exception as e:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )
        return adapter._parse_query_response(raw, broker_ref, now)

    def cancel_order_sync(
        self,
        broker_ref: str,
        adapter: AbstractAdapter,
    ) -> BrokerResponse:
        """
        Synchronously cancel an order via the adapter's Tier-3 layers.

        Composes adapter._build_cancel_payload → _do_request_cancel →
        _parse_cancel_response. Blocks the caller thread for one broker
        roundtrip. Async cancel (queued through the worker) is the subject
        of #318 and uses the same Tier-3 surface.

        Args:
            broker_ref: Broker order reference to cancel
            adapter: Live-capable adapter

        Returns:
            BrokerResponse (REJECTED on transport error, CANCELLED on success)
        """
        now = datetime.now(timezone.utc)
        payload = adapter._build_cancel_payload(broker_ref)
        try:
            raw = adapter._do_request_cancel(payload)
        except Exception as e:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )
        return adapter._parse_cancel_response(raw, broker_ref, now)
