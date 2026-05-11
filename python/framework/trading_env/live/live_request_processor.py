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
- Outcome handling (mark_filled / mark_rejected): inherited from
  LiveOrderTracker semantics; remove from pending and return the
  PendingOrder so the caller can fill the portfolio.
- Live-specific lookups (get_by_broker_ref, get_broker_ref,
  update_broker_ref) and timeout detection (check_timeouts).

V1 mode: synchronous. The worker-thread and inbox pattern are
activated in a later refactor step. For now the orchestrators block
the caller thread on the underlying transport call — observable
behavior is identical to the legacy LiveOrderTracker + adapter call
path that this class replaces.
"""

import queue
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_pending_order_manager import AbstractPendingOrderManager
from python.framework.trading_env.adapters.abstract_adapter import AbstractAdapter
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
    TimeoutConfig,
)
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderType,
)


class LiveRequestProcessor(AbstractPendingOrderManager):
    """
    Live-side request processor and pending order manager.

    Combines storage (inherited from AbstractPendingOrderManager) with
    high-level submit orchestration that drives the adapter's Tier-3
    layers. Replaces LiveOrderTracker — same storage semantics, plus
    submit_open_order and submit_close_order on top.

    The class is introduced as a skeleton in this refactor step and is
    not yet wired into LiveTradeExecutor. The wiring happens in the next
    step. This file is a no-op for the running system until then.
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

        # Worker-thread infrastructure for async dispatch.
        # In V1 the worker is started by the executor but receives no jobs
        # yet — submit/modify/cancel still run synchronously through the
        # orchestrator methods. Async submit is activated in a later
        # refactor step; until then the worker is intentionally idle.
        self._http_outbox: queue.Queue = queue.Queue()
        self._http_inbox: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_running: bool = False

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
    # Pure Storage (renamed from LiveOrderTracker for clarity)
    # ============================================

    def register_pending_open(
        self,
        order_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        broker_ref: str,
        order_kwargs: Optional[Dict] = None,
    ) -> str:
        """
        Track a submitted OPEN order with broker reference.

        Pure storage — the broker call has already happened (via the
        orchestrator or upstream code). Order enters PENDING state and
        is monitored for fill/timeout. Equivalent to the legacy
        LiveOrderTracker.submit_order with a clearer name.

        Args:
            order_id: Internal order identifier
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Position size
            broker_ref: Broker's order reference ID
            order_kwargs: Optional order parameters (stop_loss, take_profit, comment)

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
        )

        self.store_order(pending)
        self._broker_ref_index[broker_ref] = order_id

        self.logger.info(
            f"Live order tracked: {order_id} (broker_ref={broker_ref}) "
            f"timeout_at={timeout_at.isoformat()}"
        )

        return order_id

    def register_pending_close(
        self,
        position_id: str,
        broker_ref: str,
        close_lots: Optional[float] = None,
    ) -> str:
        """
        Track a submitted CLOSE order with broker reference.

        Pure storage. Equivalent to the legacy
        LiveOrderTracker.submit_close_order with a clearer name.

        Args:
            position_id: Position to close (used as order_id)
            broker_ref: Broker's order reference ID
            close_lots: Lots to close (None = close all)

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
        )

        self.store_order(pending)
        self._broker_ref_index[broker_ref] = position_id

        self.logger.info(
            f"Live close order tracked: {position_id} (broker_ref={broker_ref})"
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
    # Activated in this refactor step but intentionally idle: the worker
    # loop pulls from _http_outbox but no caller enqueues jobs yet. Async
    # submit (and later async modify/cancel) move into the worker in a
    # later refactor step. The infrastructure is added now so the surface
    # (start_worker, stop_worker, drain_inbox) is stable when the async
    # pivot happens.
    # ============================================

    def start_worker(self) -> None:
        """
        Start the daemon worker thread. Idempotent — safe to call more than once.

        The worker stays idle until async dispatch is activated.
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

        Idle in V1 — no jobs are enqueued. The short get() timeout keeps the
        shutdown latency bounded (max one timeout interval after stop_worker).
        Async dispatch logic is layered in here in a later refactor step.
        """
        while self._worker_running:
            try:
                # In V1 nothing is ever enqueued, so this raises queue.Empty
                # every 0.5s. Future: dispatch SubmitJob / EditJob / CancelJob.
                _job = self._http_outbox.get(timeout=0.5)
            except queue.Empty:
                continue

    def drain_inbox(self) -> None:
        """
        Process all pending worker responses. Called from the main thread per tick.

        In V1 the inbox is always empty (worker enqueues nothing). The call is
        wired in now so the executor's tick-loop integration is stable when
        async dispatch lands and responses start flowing.
        """
        while True:
            try:
                _response = self._http_inbox.get_nowait()
                # Future: dispatch by response type to fill / notify / etc.
            except queue.Empty:
                break
