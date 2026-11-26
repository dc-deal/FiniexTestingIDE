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

The worker thread survives every job (#669): a job that raises is logged at ERROR and answered
as unknown — a write UNRESOLVED, for the resolution to ask about, a read as a failed read — and
the worker takes the next job.
"""

import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from python.framework.exceptions.live_execution_errors import UnreadableVenueAnswerError
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_pending_order_manager import AbstractPendingOrderManager
from python.framework.trading_env.adapters.abstract_adapter import AbstractAdapter
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import ConnectionOutcome
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
    OrderResolveJob,
    OrderResolveResponse,
    PositionModifyJob,
    PositionModifyResponse,
    QueryJob,
    QueryResponse,
    SubmitJob,
    SubmitResponse,
    TradesQueryJob,
    TradesQueryResponse,
)
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOperation,
    PendingOrder,
    PendingOrderAction,
    PendingOrderTiming,
)
from python.framework.types.trading_env_types.order_event_types import OrderOperation
from python.framework.types.trading_env_types.order_types import (
    RESTING_ORDER_TYPES,
    OrderDirection,
    OrderType,
    RejectionReason,
)
from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata
from python.framework.utils.connection_ladder import ConnectionLadder


# The adapter calls a position modify is made of; no adapter implements them yet (#209)
_POSITION_MODIFY_METHODS = (
    'build_position_modify_payload',
    'do_request_position_modify',
    'parse_position_modify_response',
)


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

    def __init__(
        self,
        logger: AbstractLogger,
        timeout_config: TimeoutConfig,
        rest_ladder: Optional[ConnectionLadder] = None,
    ):
        """
        Initialize the live request processor.

        Args:
            logger: Logger instance
            timeout_config: Timeout thresholds for order monitoring
            rest_ladder: Classification for broker transport failures (#473). Without it
                every failure would keep reading as a venue rejection, which is what turns
                a lost answer into an orphan order
        """
        super().__init__(logger)
        self._timeout_config = timeout_config
        self._rest_ladder = rest_ladder or ConnectionLadder(
            name='broker_rest',
            policy=ConnectionPolicy(),
            logger=logger,
        )

        # Broker ref → order_id index for O(1) lookup when responses arrive
        self._broker_ref_index: Dict[str, str] = {}
        # The references whose status read already failed for a reason the ladder does not
        # call transient — said once per order, because the poll asks again every cycle
        self._terminal_read_failures_reported: Set[str] = set()

        # Worker-thread infrastructure for async dispatch. The worker
        # consumes SubmitJob (and later EditJob/CancelJob — #318) from
        # _http_outbox, performs the broker-side transport, and pushes
        # SubmitResponse objects into _http_inbox. drain_inbox() on the
        # main thread consumes responses and invokes the executor hooks.
        self._http_outbox: queue.Queue = queue.Queue()
        self._http_inbox: queue.Queue = queue.Queue()
        # Told that an answer was put in the inbox, so the loop drains it on arrival (#461); None
        # until the loop sets one. A listener that fails is said once
        self._on_arrival: Optional[Callable[[], None]] = None
        self._arrival_failure_reported = False
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_running: bool = False
        # A worker whose stop timed out, still inside a call: the adapter's lock is held until
        # that call returns, so a synchronous call meanwhile waits behind it
        self._lingering_worker: Optional[threading.Thread] = None
        # Once the session is over (#642): a submit is kept here instead of the outbox, so no
        # worker sends it, and take_unsent_submits hands it back to be withdrawn
        self._holding_submits: bool = False
        self._held_submits: List[SubmitJob] = []

        # Executor hooks invoked from drain_inbox() on the main thread.
        # Registered by LiveTradeExecutor in its __init__ via
        # set_executor_hooks(). All hooks run on the main thread.
        # MARKET responses are dispatched inside the processor (fill_open
        # / fill_close / rejection hooks). RESTING responses are forwarded
        # to resting_response_hook so the executor can update its
        # _active_limit_orders / _active_stop_orders (Hybrid pattern — shared storage).
        # #318 — modify/cancel/position-modify responses forwarded to
        # their respective hooks since the target order/position lives in
        # the executor's _active_*_orders / portfolio (Hybrid pattern).
        self._fill_open_hook: Optional[
            Callable[[PendingOrder, Optional[float], Optional[float]], None]] = None
        self._fill_close_hook: Optional[
            Callable[[PendingOrder, Optional[float], Optional[float]], None]] = None
        self._rejection_hook: Optional[
            Callable[[PendingOrder, RejectionReason, str, Optional[str]], None]] = None
        self._resting_response_hook: Optional[Callable[[str, 'BrokerResponse'], None]] = None
        self._modify_response_hook: Optional[Callable[[str, 'BrokerResponse'], None]] = None
        self._cancel_response_hook: Optional[Callable[[str, 'BrokerResponse'], None]] = None
        self._position_modify_response_hook: Optional[Callable[[str, 'BrokerResponse'], None]] = None
        # #326: trades-query drain handler — main-thread callback receiving
        # the parsed TradesQueryResponse so the executor can hand it to its
        # consumers; it books nothing (#508).
        self._trades_response_hook: Optional[Callable[[TradesQueryResponse], None]] = None
        # #320: status-poll drain handler — main-thread callback receiving the
        # parsed QueryResponse so the executor can clear in_flight_query, run
        # the stale-broker_ref guard, and resolve FILLED / terminal / pending
        # states from the broker's authoritative view.
        self._query_response_hook: Optional[Callable[[QueryResponse], None]] = None
        # #487: resolution drain handler — main-thread callback receiving what the venue
        # said about a write whose answer was lost, so the executor can clear the in-flight
        # flag and decide RESOLVED_RESTING / RESOLVED_ABSENT / keep asking.
        self._order_resolve_hook: Optional[Callable[[OrderResolveResponse], None]] = None
        # #362: order-event hooks — the venue took a MARKET order or close, its submit answer
        # was lost, and how an order that is gone ended (for an answer that names it later).
        # The executor records; this processor has no clock and no stream of its own.
        self._accepted_hook: Optional[Callable[[PendingOrder], None]] = None
        self._unresolved_hook: Optional[
            Callable[[PendingOrder, OrderOperation, Optional[str]], None]] = None
        self._describe_ending_hook: Optional[Callable[[str], str]] = None

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

        Composes adapter Tier-3 layers: build_submit_payload →
        do_request_submit → parse_submit_response. Pure orchestrator —
        no storage side-effect. The caller decides whether to register
        the result via register_pending_open (MARKET path) or to track
        it elsewhere (LIMIT → _active_limit_orders, migrated in step 7).

        Args:
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Order size
            order_type: MARKET, LIMIT, STOP or STOP_LIMIT
            adapter: Live-capable adapter (must implement Tier-3 layers)
            **kwargs: the WIRE-side names, which are not the strategy-facing ones —
                `limit_price` (LIMIT and STOP_LIMIT) and `stop_price` (STOP and
                STOP_LIMIT), where an `OpenOrderRequest` calls the first one `price`.
                `open_order` performs that translation; a caller reaching this method
                directly does it itself. Plus stop_loss, take_profit, comment,
                expected_price. A wrong name here used to be invisible — the payload
                builder omitted the field and the venue refused the order naming its OWN
                parameter; it now raises (#500)

        Returns:
            BrokerResponse from the adapter — UNRESOLVED on a transport fault or an answer
            that could not be read, REJECTED on the venue's refusal
        """
        payload = adapter.build_submit_payload(
            symbol=symbol,
            direction=direction,
            lots=lots,
            order_type=order_type,
            **kwargs,
        )

        # The payload above is built before the call, so a build fault still raises into the
        # caller (#500); the call and the reading of its answer are told apart (#669)
        return self._write_answer(
            'submit', '',
            call=lambda _: adapter.do_request_submit(payload),
            parse=lambda raw, received: adapter.parse_submit_response(
                raw, ts_init=received, direction=direction, order_type=order_type),
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
            BrokerResponse from the adapter — UNRESOLVED on a transport fault or an answer
            that could not be read, REJECTED on the venue's refusal
        """
        payload = adapter.build_submit_payload(
            symbol=symbol,
            direction=close_direction,
            lots=close_lots,
            order_type=OrderType.MARKET,
            **kwargs,
        )

        # As the open: the build raises into the caller, the call and its answer are told apart
        return self._write_answer(
            'submit', '',
            call=lambda _: adapter.do_request_submit(payload),
            parse=lambda raw, received: adapter.parse_submit_response(
                raw, ts_init=received, direction=close_direction, order_type=OrderType.MARKET),
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
        client_order_id: Optional[str] = None,
        entry_time: Optional[datetime] = None,
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
            client_order_id: The wire key this order is sent under (#487). Recorded so the
                        reconciler and the resolution path read it instead of re-deriving it
            entry_time: The submission on the canonical clock, which only the executor holds.
                        It is an event time, so the wall-clock reading beside it is never a
                        substitute; None leaves it unset

        Returns:
            order_id for chaining
        """
        now = datetime.now(timezone.utc)
        submitted_monotonic = time.monotonic()

        pending = PendingOrder(
            pending_order_id=order_id,
            order_action=PendingOrderAction.OPEN,
            timing=PendingOrderTiming(
                submitted_at=now,
                order_timeout_deadline_monotonic=(
                    submitted_monotonic + self._timeout_config.order_timeout_seconds),
                submitted_monotonic=submitted_monotonic),
            broker_ref=broker_ref,
            client_order_id=client_order_id,
            symbol=symbol,
            direction=direction,
            lots=lots,
            entry_time=entry_time,
            order_kwargs=order_kwargs or {},
            submission=submission if submission else SubmissionMetadata(),
        )

        self.store_order(pending)
        if broker_ref is not None:
            self._broker_ref_index[broker_ref] = order_id

        ref_str = broker_ref if broker_ref is not None else 'awaiting confirmation'
        self.logger.info(
            f'Live order tracked: {order_id} (broker_ref={ref_str}) '
            f'fill timeout {self._timeout_config.order_timeout_seconds:.0f}s'
        )

        return order_id

    def register_pending_close(
        self,
        position_id: str,
        broker_ref: Optional[str],
        close_lots: Optional[float] = None,
        submission: Optional[SubmissionMetadata] = None,
        close_reason: Optional[CloseReason] = None,
        client_order_id: Optional[str] = None,
        symbol: Optional[str] = None,
        position_direction: Optional[OrderDirection] = None,
        exit_level: Optional[float] = None,
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
            close_reason: Why the close was requested. Stored here because a
                          live close is asynchronous — the trigger and the fill
                          are a broker round trip apart (#500).
            client_order_id: The wire key this close is sent under (#487). It is NOT
                          derivable from position_id any more — a close mints its own
                          counter so an entry and its close stop sharing one key.
            symbol: The position's symbol — so an ending recorded after the position is
                          gone still names it (#362)
            position_direction: The position's direction, for the same reason
            exit_level: The level an own exit fired at (#625), stored for the same reason as
                          the close reason

        Returns:
            position_id for chaining
        """
        now = datetime.now(timezone.utc)
        submitted_monotonic = time.monotonic()

        pending = PendingOrder(
            pending_order_id=position_id,
            order_action=PendingOrderAction.CLOSE,
            closed_position_direction=position_direction,
            timing=PendingOrderTiming(
                submitted_at=now,
                order_timeout_deadline_monotonic=(
                    submitted_monotonic + self._timeout_config.order_timeout_seconds),
                submitted_monotonic=submitted_monotonic),
            broker_ref=broker_ref,
            client_order_id=client_order_id,
            symbol=symbol,
            close_lots=close_lots,
            close_reason=close_reason,
            exit_level=exit_level,
            submission=submission if submission else SubmissionMetadata(),
        )

        self.store_order(pending)
        if broker_ref is not None:
            self._broker_ref_index[broker_ref] = position_id

        ref_str = broker_ref if broker_ref is not None else 'awaiting confirmation'
        self.logger.info(
            f'Live close order tracked: {position_id} (broker_ref={ref_str})'
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
                f'mark_filled: unknown broker_ref={broker_ref}'
            )
            return None

        pending = self.remove_order(order_id)
        if pending is None:
            self.logger.warning(
                f'mark_filled: order_id={order_id} not in pending cache'
            )
            return None

        price_str = f'{fill_price:.5f}' if fill_price is not None else 'N/A'
        self.logger.info(
            f'Order filled: {order_id} at {price_str} '
            f'({filled_lots} lots, broker_ref={broker_ref})'
        )

        # A fill price of zero is never a real price. It used to be an EXPECTED dry-run
        # answer — the simulator had no quote and fell back to 0.0 — so this line named
        # dry-run mode as the cause. That is no longer true (#505): a dry-run order either
        # fills at a real quote or refuses and stays PENDING. So a zero here now means
        # something upstream produced one, and pointing at dry-run would send the reader
        # looking in the one place it cannot be.
        if fill_price is not None and fill_price == 0.0:
            self.logger.warning(
                f'⚠️  Fill price is 0.00000 for {order_id} — no venue prices anything at '
                f'zero, so this came from our side. Every P&L figure derived from this '
                f'fill is wrong. Check what answered the fill for this order.'
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
                f'mark_rejected: unknown broker_ref={broker_ref}'
            )
            return None

        pending = self.remove_order(order_id)
        if pending is None:
            self.logger.warning(
                f'mark_rejected: order_id={order_id} not in pending cache'
            )
            return None

        self.logger.warning(
            f'Order rejected: {order_id} reason={reason} '
            f'(broker_ref={broker_ref})'
        )

        return pending

    def index_broker_ref(self, broker_ref: str, order_id: str) -> None:
        """
        Record a venue reference that arrived late, so lookups by it find their order.

        The ordinary submit path indexes as it confirms; this is for the paths where the
        reference arrives by another route — the reconcile attribution and #487's
        resolution — which would otherwise leave an order findable by id but not by the
        name the venue uses for it.

        The index maps a reference to an order THIS STORE HOLDS, and every reader relies on
        that: `get_by_broker_ref` resolves the id against `_pending_orders` and answers None
        when it is absent, which is indistinguishable from "no such reference". A RESTING
        order lives in the executor's lists, not here, so indexing one would plant a pointer
        into nothing — and `mark_filled` would pop it, fail to find the order, and warn
        about a cache miss that was really a filing error. The invariant is enforced here
        because this is the only place a late reference can break it.

        Args:
            broker_ref: The venue's reference
            order_id: Internal pending-order identifier it belongs to
        """
        if order_id not in self._pending_orders:
            return
        self._broker_ref_index[broker_ref] = order_id

    def discard_order(
        self,
        order_id: str,
        reason: str,
    ) -> Optional[PendingOrder]:
        """
        Remove a pending order by its own id, whatever its broker reference is.

        The reference-keyed removals above cannot serve an order whose write was never
        answered: its broker_ref is None, so the index lookup finds nothing and they return
        BEFORE removing anything. The order id always exists.

        Args:
            order_id: Internal pending-order identifier
            reason: Why the order is being discarded, for the log line

        Returns:
            PendingOrder if it was still tracked, None otherwise
        """
        pending = self.remove_order(order_id)
        if pending is None:
            return None

        if pending.broker_ref is not None:
            self._broker_ref_index.pop(pending.broker_ref, None)

        self.logger.warning(
            f'Pending order discarded: {order_id} reason={reason} '
            f'(broker_ref={pending.broker_ref or "never answered"})'
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
            List of PendingOrder objects past their order timeout deadline
        """
        now = time.monotonic()
        timed_out = []

        for pending in self._pending_orders.values():
            deadline = pending.timing.order_timeout_deadline_monotonic
            if deadline is not None and deadline <= now:
                timed_out.append(pending)

        return timed_out

    # ============================================
    # Broker Reference Update
    # ============================================

    def update_broker_ref(self, old_ref: str, new_ref: str) -> bool:
        """
        Update broker_ref after broker-side order replacement.

        A venue that re-mints the reference on a modification answers with a
        new one that replaces the original. The caller is responsible for
        invoking this when such a swap occurs.

        A miss is NOT an anomaly here, and saying it was is what this warning used to do.
        The index covers the orders this store holds; a RESTING order lives in the
        executor's lists and never had an entry to update, so its amend legitimately finds
        nothing — the executor writes the new reference onto the pending itself. The line
        could not fire on Kraken, whose AmendOrder keeps the txid, so it was waiting for the
        second adapter (#209) to report a correct operation as a fault. It is now a DEBUG
        note, and the return value still tells the caller what happened.

        Args:
            old_ref: Previous broker reference (now invalid)
            new_ref: New broker reference from broker

        Returns:
            True if old_ref was found and updated, False otherwise
        """
        order_id = self._broker_ref_index.pop(old_ref, None)
        if order_id is None:
            self.logger.debug(
                f'update_broker_ref: {old_ref} is not in this store\'s index — expected for '
                f'a resting order, whose reference the executor owns'
            )
            return False

        self._broker_ref_index[new_ref] = order_id

        pending = self._pending_orders.get(order_id)
        if pending is not None:
            pending.broker_ref = new_ref

        self.logger.info(
            f'Broker ref updated: {order_id} ({old_ref} → {new_ref})'
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

    def clear_pending(self, reason: str = 'scenario_end') -> List[PendingOrder]:
        """
        Clear all pending orders and the broker_ref index.

        Args:
            reason: Why they are cleared, for the log line

        Returns:
            The orders cleared, so the executor can book how each one ended
        """
        cleared = super().clear_pending(reason=reason)
        self._broker_ref_index.clear()
        return cleared

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
        fill_open: Callable[[PendingOrder, Optional[float], Optional[float]], None],
        fill_close: Callable[[PendingOrder, Optional[float], Optional[float]], None],
        on_rejection: Callable[[PendingOrder, RejectionReason, str, Optional[str]], None],
        resting_response: Optional[Callable[[str, BrokerResponse], None]] = None,
        modify_response: Optional[Callable[[str, BrokerResponse], None]] = None,
        cancel_response: Optional[Callable[[str, BrokerResponse], None]] = None,
        position_modify_response: Optional[Callable[[str, BrokerResponse], None]] = None,
        trades_response: Optional[Callable[[TradesQueryResponse], None]] = None,
        query_response: Optional[Callable[[QueryResponse], None]] = None,
        order_resolve: Optional[Callable[[OrderResolveResponse], None]] = None,
        on_accepted: Optional[Callable[[PendingOrder], None]] = None,
        on_unresolved: Optional[
            Callable[[PendingOrder, OrderOperation, Optional[str]], None]] = None,
        describe_ending: Optional[Callable[[str], str]] = None,
    ) -> None:
        """
        Register executor callbacks for async outcomes.

        Invoked by LiveTradeExecutor in its __init__. Hooks are called from
        the main thread inside drain_inbox(), never from the worker thread,
        so they may safely mutate portfolio / order_history / listener state
        without locking.

        Args:
            fill_open: (pending, fill_price, reported_fee) — books an OPEN filled in its
                       own submit answer (portfolio add, history append); reported_fee is the
                       fee that answer carries, None where it carries none (#508)
            fill_close: (pending, fill_price, reported_fee) — the same for a CLOSE
            on_rejection: _record_processor_rejection(pending, reason, message,
                          venue_reason) — handles MARKET broker-side rejection (counter,
                          history, listener notification). The executor builds
                          the record: it owns the canonical clock the
                          rejection is stamped with, and this processor does not
            resting_response: Optional — _handle_resting_submit_response(order_id,
                            broker_response). Invoked for LIMIT / STOP / STOP_LIMIT
                            submit responses so the executor can update its
                            _active_limit_orders / _active_stop_orders (Hybrid pattern).
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
                            hand the venue's executions to the consumers that
                            asked for them. It books nothing and writes nothing
                            onto the order (#508).
            query_response: Optional (#320) — _handle_query_response(response).
                            Invoked for QueryResponse so the executor can clear
                            in_flight_query on the polled PendingOrder, apply
                            the stale-broker_ref guard, and react to FILLED /
                            terminal / pending broker outcomes.
            order_resolve: _handle_order_resolve_response(response) — invoked for
                            OrderResolveResponse so the executor can clear the
                            resolution in-flight flag and apply the verdict (#487).
            on_accepted: _record_acceptance(pending) — the venue answered a MARKET order or
                            close with its reference (#362)
            on_unresolved: _record_unresolved(pending, operation, message) — the answer to
                            a MARKET submit was lost (#362)
            describe_ending: describe_recent_ending(order_id) — how an order that is no
                            longer tracked ended, for the warning about a late answer
        """
        self._fill_open_hook = fill_open
        self._fill_close_hook = fill_close
        self._rejection_hook = on_rejection
        self._resting_response_hook = resting_response
        self._modify_response_hook = modify_response
        self._cancel_response_hook = cancel_response
        self._position_modify_response_hook = position_modify_response
        self._trades_response_hook = trades_response
        self._query_response_hook = query_response
        self._order_resolve_hook = order_resolve
        self._accepted_hook = on_accepted
        self._unresolved_hook = on_unresolved
        self._describe_ending_hook = describe_ending

    def set_arrival_listener(self, listener: Callable[[], None]) -> None:
        """
        Name what to tell when an answer was put in the inbox.

        Args:
            listener: Called on the worker thread once the answer is in the inbox; it must only
                record and signal, never touch loop state. It stamps the arrival itself, on the
                session's own axis: a replay's time is not the wall clock this thread could read
        """
        self._on_arrival = listener

    def _put_inbox(self, response: object) -> None:
        """
        Put an answer in the inbox for the main thread, then tell the listener it arrived.

        The listener runs on this worker thread, and nothing here may end it: a failing listener
        costs the wake, never the worker — the answer already lies in the inbox, and the loop's
        next pass drains it. A worker that died here would leave every later write unsent (#461).

        Args:
            response: The response object, drained by `drain_inbox` on the main thread
        """
        self._http_inbox.put(response)
        if self._on_arrival is None:
            return
        try:
            self._on_arrival()
        except Exception as error:  # noqa: BLE001 — a lost wake costs latency, never the worker
            if not self._arrival_failure_reported:
                self._arrival_failure_reported = True
                self.logger.error(
                    f'📡 The loop could not be told that a venue answer arrived '
                    f'({type(error).__name__}: {error}). The answer is in the inbox and is read '
                    f'on the next pass; the request worker goes on. Said once.')

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

    def stop_worker(self, timeout: float) -> None:
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
                self._lingering_worker = self._worker_thread
                self.logger.warning(
                    f'Worker thread did not shut down within {timeout}s — '
                    f'continuing (daemon)'
                )
            else:
                self.logger.info('Worker thread stopped cleanly')
        self._worker_thread = None

    def is_worker_call_lingering(self) -> bool:
        """
        Whether a stopped worker is still inside a call to the venue, because its stop timed out.

        Returns:
            True while that thread is alive
        """
        return self._lingering_worker is not None and self._lingering_worker.is_alive()

    def take_unsent_submits(self) -> List[SubmitJob]:
        """
        Take every job off the outbox that a stopped worker never took, and hand back the submits.

        Such a job never left this process, and once taken here no worker can send it. The other
        jobs are dropped: a stopped worker would never send them either, and the session's cleanup
        reads and cancels by itself. Only for a stopped worker: a running one would lose what it
        was about to send.

        A submit held back once the session was over (#642) is handed back too, whether the worker
        still runs or not: it never reached the outbox.

        Returns:
            The submits among them, oldest first; while the worker still runs, the held ones only
        """
        held, self._held_submits = self._held_submits, []
        if self._worker_running:
            return held
        submits: List[SubmitJob] = held
        while True:
            try:
                job = self._http_outbox.get_nowait()
            except queue.Empty:
                return submits
            if isinstance(job, SubmitJob):
                submits.append(job)

    def _worker_loop(self) -> None:
        """
        Daemon worker loop. Pulls jobs from _http_outbox and dispatches them.

        Job types (#318 + #319):
        - SubmitJob: submit a new order (open or close)
        - EditJob: modify a pending order
        - CancelJob: cancel a pending order
        - PositionModifyJob: modify an open position's SL/TP
        - OrderResolveJob: ask what became of a write whose answer was lost (#487)

        The short get() timeout keeps shutdown latency bounded (max one
        timeout interval after stop_worker).

        No exception a job raises ends this thread (#669): every order after it would wait in
        the outbox, unsent, while the processor still believed its worker ran. A SystemExit or
        a KeyboardInterrupt still ends it, as it should. A job that raises
        is answered by `_answer_crashed_job` and the loop takes the next one; the thread ends
        only through `stop_worker()`.
        """
        while self._worker_running:
            try:
                job = self._http_outbox.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                self._dispatch(job)
            except Exception as error:  # noqa: BLE001 — a crashed job costs its answer, never the worker
                self._answer_crashed_job(job, error)

    def _dispatch(self, job: object) -> None:
        """
        Hand one outbox job to its dispatcher.

        Args:
            job: The job taken from the outbox
        """
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
        elif isinstance(job, OrderResolveJob):
            self._dispatch_order_resolve_job(job)
        else:
            self.logger.warning(
                f'Unknown job type in outbox: {type(job).__name__}'
            )

    def _answer_crashed_job(self, job: object, error: BaseException) -> None:
        """
        Answer a job whose dispatcher raised, so its order is not left waiting (#669).

        Every dispatcher puts its answer once, as its last step, so a dispatcher that raised
        has put nothing and this answer is never a second one. A write is answered UNRESOLVED
        — never through `_failure_response`, which reads an exception as the venue's refusal
        and would drop an order the venue may already hold — so the resolution asks the venue.
        A read is answered as a failed read. Should even that answer fail to build, the job
        stays unanswered and the worker still goes on.

        Args:
            job: The job whose dispatcher raised
            error: What it raised
        """
        subject = getattr(job, 'order_id', None) or getattr(job, 'position_id', None) or '?'
        self.logger.error(
            f'📡 The request worker crashed on a {type(job).__name__} for {subject} '
            f'({type(error).__name__}: {error}). The worker goes on with the next job; this '
            f'one is answered as unknown, so a write is resolved by asking the venue and a '
            f'read is asked again.')
        try:
            answer = self._crashed_answer(job, f'{type(error).__name__}: {error}')
        except Exception as second:  # noqa: BLE001 — the worker survives its own answer too
            self.logger.error(
                f'📡 The answer to the crashed {type(job).__name__} for {subject} could not be '
                f'built either ({type(second).__name__}: {second}); it stays unanswered.')
            return
        if answer is not None:
            self._put_inbox(answer)

    def _crashed_answer(self, job: object, reason: str) -> Optional[object]:
        """
        The answer a crashed job gets, by its kind.

        Args:
            job: The job whose dispatcher raised
            reason: What it raised, for the answer's record

        Returns:
            The inbox item to put, or None for a job kind this worker does not know
        """
        now = datetime.now(timezone.utc)
        reason = f'the request worker crashed on this job: {reason}'
        if isinstance(job, SubmitJob):
            return SubmitResponse(
                order_id=job.order_id, action=job.action, order_type=job.order_type,
                broker_response=self._unknown_outcome(broker_ref='', reason=reason, ts_init=now))
        if isinstance(job, EditJob):
            return EditResponse(order_id=job.order_id, broker_response=self._unknown_outcome(
                broker_ref=job.broker_ref, reason=reason, ts_init=now))
        if isinstance(job, CancelJob):
            return CancelResponse(order_id=job.order_id, broker_response=self._unknown_outcome(
                broker_ref=job.broker_ref, reason=reason, ts_init=now))
        if isinstance(job, PositionModifyJob):
            return PositionModifyResponse(
                position_id=job.position_id, broker_response=self._unknown_outcome(
                    broker_ref=job.position_id, reason=reason, ts_init=now))
        if isinstance(job, TradesQueryJob):
            return TradesQueryResponse(
                order_id=job.order_id, broker_ref=job.broker_ref, success=False,
                error_message=reason)
        if isinstance(job, QueryJob):
            return QueryResponse(order_id=job.order_id, broker_response=self._unknown_outcome(
                broker_ref=job.broker_ref, reason=reason, ts_init=now))
        if isinstance(job, OrderResolveJob):
            return OrderResolveResponse(order_id=job.order_id, success=False, error_message=reason)
        return None

    @staticmethod
    def _unknown_outcome(broker_ref: str, reason: str, ts_init: datetime) -> BrokerResponse:
        """
        An UNRESOLVED answer: the venue's state of this order is not known.

        Args:
            broker_ref: The reference the request was about ('' for a submit that never got one)
            reason: Why it is not known, for the answer's record
            ts_init: When this process gave the answer (UTC)

        Returns:
            A BrokerResponse carrying UNRESOLVED
        """
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.UNRESOLVED,
            rejection_reason=reason,
            ts_init=ts_init,
        )

    def _write_answer(
        self,
        operation: str,
        broker_ref: str,
        call: Callable[[Any], Any],
        parse: Callable[[Any, datetime], BrokerResponse],
        build: Optional[Callable[[], Any]] = None,
    ) -> BrokerResponse:
        """
        A write's answer, with its three stages told apart (#669).

        Only what the VENUE refused is a refusal, so where an exception raised decides:
            build  nothing was sent — REJECTED, said as "not sent"
            call   the ladder decides, as since #473: a transport fault is UNRESOLVED, anything
                   else is the venue's refusal — except an answer the adapter could not read,
                   which is UNRESOLVED
            parse  the call returned, so the venue may hold the order — UNRESOLVED
        An UNRESOLVED write is resolved by asking the venue (#487), never by sending it again.

        Args:
            operation: What is attempted, for the line the operator reads
            broker_ref: The reference the write is about ('' for a submit)
            call: The adapter's request, given the built payload
            parse: The adapter's parse, given the raw answer and its receipt
            build: The adapter's payload builder, or None where the payload is already built

        Returns:
            The BrokerResponse the dispatcher puts
        """
        payload = None
        if build is not None:
            try:
                payload = build()
            except Exception as error:  # noqa: BLE001 — nothing was sent, said below
                self.logger.error(
                    f'📡 The {operation} for {broker_ref or "a new order"} was not sent: its '
                    f'request could not be built ({type(error).__name__}: {error}). Nothing '
                    f'reached the venue, so it is answered as refused.')
                return BrokerResponse(
                    broker_ref=broker_ref,
                    status=BrokerOrderStatus.REJECTED,
                    rejection_reason=f'not sent: {error}',
                    ts_init=datetime.now(timezone.utc),
                )
        try:
            raw = call(payload)
        except Exception as error:  # noqa: BLE001 — classified by _failure_response
            return self._failure_response(
                error, broker_ref=broker_ref, ts_init=datetime.now(timezone.utc),
                operation=operation)
        # The answer's receipt, read as it comes back — never the moment the request left
        received = datetime.now(timezone.utc)
        try:
            return parse(raw, received)
        except Exception as error:  # noqa: BLE001 — the venue answered, said below
            return self._unreadable_answer(error, broker_ref, operation, received)

    def _unreadable_answer(
        self,
        error: BaseException,
        broker_ref: str,
        operation: str,
        ts_init: datetime,
    ) -> BrokerResponse:
        """
        The answer to a write the venue answered and we could not read (#669).

        Args:
            error: What reading the answer raised
            broker_ref: The reference the write is about ('' for a submit)
            operation: What was attempted, for the line the operator reads
            ts_init: When this process received the answer (UTC)

        Returns:
            A BrokerResponse carrying UNRESOLVED
        """
        self.logger.error(
            f'📡 The venue answered the {operation} for {broker_ref or "a new order"}, and '
            f'the answer could not be read ({type(error).__name__}: {error}). The venue may '
            f'hold the order, so it is not treated as refused: UNRESOLVED, and the resolution '
            f'asks the venue.')
        return self._unknown_outcome(
            broker_ref=broker_ref, reason=f'answer unreadable: {error}', ts_init=ts_init)

    def _failure_response(
        self,
        error: BaseException,
        broker_ref: str,
        ts_init: datetime,
        operation: str,
        self_healing: bool = False,
        is_read: bool = False,
    ) -> BrokerResponse:
        """
        Turn a failed broker call into the response that says what actually happened.

        The whole point of #473 in one method: a TRANSIENT transport fault becomes
        UNRESOLVED, never REJECTED. Reporting "we could not reach the venue" as "the venue
        refused you" makes the executor drop an order that may be resting at the broker —
        we manufacture the divergence #349 exists to resolve. Anything the ladder does not
        classify as transient IS an answer (a venue error, a signing fault, our own bug)
        and stays a rejection.

        The LEVEL matters as much as the outcome. A failed status poll heals itself on the
        next throttle cycle, so it is a warning; a failed submit leaves an order whose fate
        we do not know, so it is an error and belongs in the §35 pot. Grading a thirty-day
        run FINISHED_WITH_ERRORS over one 502 on a re-poll would make the outcome contract
        useless exactly where it is supposed to earn its keep.

        A failed READ is UNRESOLVED whatever the ladder says. The rejection rule above is
        right for a write — "Insufficient funds" on a submit IS the venue refusing the
        order — and wrong for a status query, whose failure says nothing about the order it
        asked about: `EAPI:Invalid nonce` (measured in the first field study) refused the
        QUESTION. Read as the order's own rejection, it dropped a market order the venue
        had filled and cleared the stamp of a stop the venue still held. For a read the
        ladder decides only the level: a transient fault is a warning, anything else an
        error, said once per order because the poll asks again every cycle.

        An answer the adapter could not read (`UnreadableVenueAnswerError`) is no refusal
        either: the venue answered, so a write it carried may have been executed. It is
        UNRESOLVED on every write path, the synchronous ones included (#669).

        Args:
            error: What the call raised
            broker_ref: The reference the call was about ('' for a submit that never got one)
            ts_init: When this process received the failure (UTC)
            operation: What was being attempted, for the line the operator reads
            self_healing: True when the caller retries this on its own cadence — then the
                failure is a warning rather than an entry in the error pot
            is_read: True for a status query — its failure is never an answer about the
                order, so it is UNRESOLVED however the ladder classifies it

        Returns:
            A BrokerResponse carrying UNRESOLVED or REJECTED
        """
        if isinstance(error, UnreadableVenueAnswerError) and not is_read:
            return self._unreadable_answer(error, broker_ref, operation, ts_init)
        transient = self._rest_ladder.classify(error) is ConnectionOutcome.TRANSIENT
        if is_read and not transient:
            if broker_ref not in self._terminal_read_failures_reported:
                self._terminal_read_failures_reported.add(broker_ref)
                self.logger.error(
                    f'📡 The {operation} for broker_ref={broker_ref} failed, and not with a '
                    f'transport fault ({error}). The ORDER is not treated as refused — the '
                    f'question was — so nothing is booked and it is asked about again on '
                    f'the next cycle. Said once for this order.')
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.UNRESOLVED,
                rejection_reason=str(error),
                ts_init=ts_init,
            )
        if transient:
            message = (
                f'📡 broker unreachable during {operation} ({error}) — outcome UNRESOLVED. '
                f'This is an external system, not the trading logic.'
            )
            if self_healing:
                self.logger.warning(f'{message} Retrying on the next cadence.')
            else:
                self.logger.error(f'{message} Resolution comes from the next query.')
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.UNRESOLVED if transient else BrokerOrderStatus.REJECTED,
            rejection_reason=str(error),
            ts_init=ts_init,
        )

    def _dispatch_submit_job(self, job: SubmitJob) -> None:
        """
        Worker-thread handler for SubmitJob.

        Performs the broker transport via the adapter's Tier-3 layers,
        wraps the result in a SubmitResponse, and pushes it to the inbox.
        A transport fault or an answer that could not be read surfaces as UNRESOLVED, the
        venue's refusal as REJECTED (#473, #669). The payload is built on the caller's thread,
        so a submit has no build stage here.

        Args:
            job: The SubmitJob enqueued by submit_open_order_async or
                 submit_close_order_async
        """
        response = self._write_answer(
            'submit', '',
            call=lambda _: job.adapter.do_request_submit(job.payload),
            parse=lambda raw, received: job.adapter.parse_submit_response(
                raw, ts_init=received, direction=job.direction, order_type=job.order_type),
        )

        self._put_inbox(SubmitResponse(
            order_id=job.order_id,
            action=job.action,
            order_type=job.order_type,
            broker_response=response,
        ))

    def _dispatch_edit_job(self, job: EditJob) -> None:
        """
        Worker-thread handler for EditJob (#318).

        Composes adapter Tier-3 modify layer:
            build_modify_payload → do_request_modify → parse_modify_response

        A transport fault or an answer that could not be read surfaces as UNRESOLVED, the
        venue's refusal and a request that could not be built as REJECTED (#473, #669). The
        main thread's drain_inbox routes the EditResponse to the executor via
        _modify_response_hook for state application.

        Args:
            job: EditJob enqueued by submit_modify_order_async
        """
        response = self._write_answer(
            'modify', job.broker_ref,
            build=lambda: job.adapter.build_modify_payload(
                broker_ref=job.broker_ref,
                symbol=job.symbol,
                order_type=job.order_type,
                new_price=job.new_price,
                new_limit_price=job.new_limit_price,
                new_stop_loss=job.new_stop_loss,
                new_take_profit=job.new_take_profit,
            ),
            call=job.adapter.do_request_modify,
            parse=lambda raw, received: job.adapter.parse_modify_response(
                raw, original_broker_ref=job.broker_ref, ts_init=received,
            ),
        )

        self._put_inbox(EditResponse(
            order_id=job.order_id,
            broker_response=response,
        ))

    def _dispatch_cancel_job(self, job: CancelJob) -> None:
        """
        Worker-thread handler for CancelJob (#318).

        Composes adapter Tier-3 cancel layer:
            build_cancel_payload → do_request_cancel → parse_cancel_response
        Its stages are told apart as a modify's are (#669).
        """
        response = self._write_answer(
            'cancel', job.broker_ref,
            build=lambda: job.adapter.build_cancel_payload(job.broker_ref),
            call=job.adapter.do_request_cancel,
            parse=lambda raw, received: job.adapter.parse_cancel_response(
                raw, job.broker_ref, received,
            ),
        )

        self._put_inbox(CancelResponse(
            order_id=job.order_id,
            broker_response=response,
        ))

    def _dispatch_position_modify_job(self, job: PositionModifyJob) -> None:
        """
        Worker-thread handler for PositionModifyJob (#318).

        Position-modify Tier-3 surface is adapter-specific. The current
        contract assumes the adapter exposes
            build_position_modify_payload(position_id, symbol, new_sl, new_tp)
            do_request_position_modify(payload)
            parse_position_modify_response(raw, position_id, ts_init)
        as a separate operation triple. Adapters that fold position-modify
        into the existing modify operation (#209 design decision) should
        override this dispatch method or expose the same triple names as
        thin aliases.

        No adapter implements the triple yet, so before anything is sent the job is refused
        as not supported — a call the adapter does not have is not a venue refusal (#669).
        """
        missing = [name for name in _POSITION_MODIFY_METHODS if not hasattr(job.adapter, name)]
        if missing:
            self.logger.error(
                f'📡 Position modify for {job.position_id} refused: '
                f'{type(job.adapter).__name__} implements no position modify '
                f'({", ".join(missing)} missing). Nothing was sent.')
            response = BrokerResponse(
                broker_ref=job.position_id,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=(f'not supported: {type(job.adapter).__name__} implements no '
                                  f'position modify — nothing was sent'),
                ts_init=datetime.now(timezone.utc),
            )
        else:
            response = self._write_answer(
                'position modify', job.position_id,
                build=lambda: job.adapter.build_position_modify_payload(
                    position_id=job.position_id,
                    symbol=job.symbol,
                    new_stop_loss=job.new_stop_loss,
                    new_take_profit=job.new_take_profit,
                ),
                call=job.adapter.do_request_position_modify,
                parse=lambda raw, received: job.adapter.parse_position_modify_response(
                    raw, position_id=job.position_id, ts_init=received,
                ),
            )

        self._put_inbox(PositionModifyResponse(
            position_id=job.position_id,
            broker_response=response,
        ))

    def _dispatch_query_job(self, job: QueryJob) -> None:
        """
        Worker-thread handler for QueryJob (#320).

        Composes adapter Tier-3 query layer:
            build_query_payload → do_request_query → parse_query_response

        A failed query is surfaced as an UNRESOLVED BrokerResponse, never as a
        rejection — the main thread's drain handler clears in_flight_query
        regardless, and the order is simply asked about again on the next
        throttle cycle.
        """
        try:
            payload = job.adapter.build_query_payload(job.broker_ref)
            raw = job.adapter.do_request_query(payload)
            received = datetime.now(timezone.utc)
            response = job.adapter.parse_query_response(
                raw, job.broker_ref, received, market=job.market)
        except Exception as e:
            response = self._failure_response(
                e, broker_ref=job.broker_ref, ts_init=datetime.now(timezone.utc),
                operation='status query', self_healing=True, is_read=True)

        self._put_inbox(QueryResponse(
            order_id=job.order_id,
            broker_response=response,
        ))

    def _dispatch_order_resolve_job(self, job: OrderResolveJob) -> None:
        """
        Worker-thread handler for OrderResolveJob (#487).

        Read-only by construction — this asks ABOUT a write, it never repeats one. Which
        route answers is decided by what the lost answer left behind, and both are measured
        (2026-09-13):

            with a broker_ref     QueryOrders by that reference — the ordinary route
            without one           OpenOrders + ClosedOrders narrowed to our key. QueryOrders
                                  refuses a key with no txid, so there is no one-call form

        A read that FAILS is reported as such rather than as an empty answer: "the venue did
        not answer" and "the venue answered and named nothing" are opposite facts, and only
        the second one may ever become a rejection.

        Args:
            job: The OrderResolveJob enqueued by the executor's resolution scheduler
        """
        try:
            answer = self._resolve_answer(job)
        except Exception as e:                           # noqa: BLE001 — reported below
            answer = OrderResolveResponse(
                order_id=job.order_id,
                success=False,
                error_message=str(e),
            )
        # Put once, after the read: a later fault can no longer add a second answer (#669)
        self._put_inbox(answer)

    def _resolve_answer(self, job: OrderResolveJob) -> OrderResolveResponse:
        """
        Ask the venue what became of a write, by the route its lost answer left.

        Args:
            job: The OrderResolveJob

        Returns:
            The answer to put — the venue's, or a failed read where no question can be asked
        """
        if job.broker_ref:
            payload = job.adapter.build_query_payload(job.broker_ref)
            raw = job.adapter.do_request_query(payload)
            parsed = job.adapter.parse_query_response(
                raw, job.broker_ref, datetime.now(timezone.utc))
            return OrderResolveResponse(
                order_id=job.order_id,
                status=None if parsed.status == BrokerOrderStatus.UNKNOWN
                else parsed.status,
                broker_ref=job.broker_ref,
                filled_lots=parsed.filled_lots,
            )

        if not job.client_order_id:
            # Sent under no key and no reference came back: there is nothing to ask
            # about. Reported as a FAILED read rather than as an empty answer, because
            # an empty answer is evidence and this is the absence of a question.
            return OrderResolveResponse(
                order_id=job.order_id,
                success=False,
                error_message='no client order id and no broker reference to ask with',
            )

        matches = self._orders_with_client_key(
            job.adapter.get_broker_orders(), job.client_order_id)
        if not matches:
            matches = self._orders_with_client_key(
                job.adapter.get_closed_broker_orders(
                    job.range_start, job.range_end, job.client_order_id),
                job.client_order_id)
        if len(matches) > 1:
            # Several orders carry our key. Picking one would be a guess about which, and
            # answering "none" would read as "the venue never took it" — a verdict that
            # books the order undelivered. A FAILED read
            # is what this is: the resolution asks again, and its ceiling ends the question.
            return OrderResolveResponse(
                order_id=job.order_id,
                success=False,
                error_message=(f'{len(matches)} orders at the venue carry client order id '
                               f'{job.client_order_id} — not guessing which'),
            )
        match = matches[0] if matches else None

        return OrderResolveResponse(
            order_id=job.order_id,
            status=match.status if match is not None else None,
            broker_ref=match.broker_ref if match is not None else None,
            filled_lots=match.filled_lots if match is not None else None,
        )

    @staticmethod
    def _orders_with_client_key(
        orders: List[BrokerOrder],
        client_order_id: str,
    ) -> List[BrokerOrder]:
        """
        Every order in a venue answer that carries this wire key.

        One key naming two orders was the measured state before a close minted its own
        counter, and picking one of them would be a guess about which — a resolution that
        guesses is worse than one that keeps asking. So the caller reads more than one as a
        question it cannot answer yet. This used to return None for several matches, and the
        caller read that None as "the venue names nothing", which is an answer: the order was
        then booked undelivered.

        Args:
            orders: What the venue reported
            client_order_id: The key we sent

        Returns:
            The matching orders, in the venue's order
        """
        return [o for o in orders if o.client_order_id == client_order_id]

    def _dispatch_trades_query_job(self, job: TradesQueryJob) -> None:
        """
        Worker-thread handler for TradesQueryJob (#326).

        Composes adapter Tier-3 trades-query layer:
            build_trades_query_payload → do_request_trades_query
            → parse_trades_query_response

        Transport errors are surfaced as a TradesQueryResponse with success=False
        and no trades list — the read found nothing out — and the main thread's drain handler
        decides how to react (typically: leave the pending in place, retry on next poll).
        """
        try:
            payload = job.adapter.build_trades_query_payload(job.broker_ref)
            raw = job.adapter.do_request_trades_query(payload)
            trades = job.adapter.parse_trades_query_response(
                raw, broker_ref=job.broker_ref, order_id=job.order_id,
            )
            answer = TradesQueryResponse(
                order_id=job.order_id,
                broker_ref=job.broker_ref,
                trades=trades,
                success=True,
            )
        except Exception as e:
            answer = TradesQueryResponse(
                order_id=job.order_id,
                broker_ref=job.broker_ref,
                success=False,
                error_message=str(e),
            )
        # Put once, after the read: a later fault can no longer add a second answer (#669)
        self._put_inbox(answer)

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
            f'flush_outbox: outbox not drained within {timeout}s'
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
            elif isinstance(item, OrderResolveResponse):
                if self._order_resolve_hook is not None:
                    self._order_resolve_hook(item)
            else:
                self.logger.warning(
                    f'Unknown response type in inbox: {type(item).__name__}'
                )

    def _handle_submit_response(self, item: SubmitResponse) -> None:
        """
        Main-thread handler for a SubmitResponse from the worker.

        RESTING responses (LIMIT, STOP, STOP_LIMIT) are delegated to the executor via
        resting_response_hook because those pendings live in the executor's
        _active_limit_orders / _active_stop_orders (shared sim/live storage per Hybrid
        pattern), not in the processor. Routing only LIMIT here left a STOP's broker_ref
        unconfirmed forever: it fell through to the MARKET branch, found nothing in
        _pending_orders, and logged an unknown-order warning while the pending rested with
        broker_ref=None — which blocks has_pending_orders() for the rest of the session.

        MARKET responses are dispatched inside the processor with three branches:
          REJECTED → remove local pending + invoke rejection hook
          FILLED   → confirm broker_ref + mark_filled + invoke fill hook
          PENDING  → confirm broker_ref (polling takes over)

        Args:
            item: The SubmitResponse delivered via _http_inbox
        """
        # RESTING path: executor owns the storage in _active_limit_orders / _active_stop_orders
        if item.order_type in RESTING_ORDER_TYPES:
            if self._resting_response_hook is not None:
                self._resting_response_hook(item.order_id, item.broker_response)
            else:
                self.logger.warning(
                    f'drain_inbox: {item.order_type.value} SubmitResponse for '
                    f'{item.order_id} but no resting_response_hook registered'
                )
            return

        # MARKET path: processor owns the storage in _pending_orders
        pending = self._pending_orders.get(item.order_id)
        if pending is None:
            ended = (self._describe_ending_hook(item.order_id)
                     if self._describe_ending_hook is not None else '')
            self.logger.warning(
                f'drain_inbox: SubmitResponse for unknown order_id {item.order_id}{ended}'
            )
            return

        response = item.broker_response

        if response.is_unresolved:
            # #473 — the venue never answered. It may or may not hold this order, so the
            # one thing we must NOT do is drop it: a forgotten resting order is an orphan,
            # and the algo must not be told the venue refused something it never saw.
            # The pending stays, marked in-flight, and the query path resolves it.
            pending.execution_state.in_flight_operation = PendingOperation.PENDING_SUBMIT
            if self._unresolved_hook is not None:
                self._unresolved_hook(
                    pending, OrderOperation.SUBMIT, response.rejection_reason)
            self.logger.error(
                f'📡 Order {item.order_id} UNRESOLVED — no usable answer from the broker '
                f'({response.rejection_reason}). Keeping it in flight; the venue may hold '
                f'it. Resolution comes from the next query, not from re-sending (#487).'
            )
            return

        if response.is_rejected:
            # Submit failed at broker — discard local pending, notify executor
            self.remove_order(item.order_id)
            if pending.broker_ref is not None:
                self._broker_ref_index.pop(pending.broker_ref, None)

            if self._rejection_hook is not None:
                self._rejection_hook(
                    pending, RejectionReason.BROKER_ERROR,
                    f"Broker rejected: {response.rejection_reason or 'unknown'}",
                    response.rejection_reason)
            return

        # Non-rejected: confirm broker_ref and update index
        pending.broker_ref = response.broker_ref
        if response.broker_ref:
            self._broker_ref_index[response.broker_ref] = item.order_id
        # #487 — the ORDINARY answer arrived after all, late. It settles the same question
        # the resolution was asking, so the resolution stops: exactly one actor resolves a
        # pending, and which one it is must not depend on ordering luck.
        pending.execution_state.resolution_deadline = None
        pending.execution_state.resolution_next_at = None
        if self._accepted_hook is not None:
            self._accepted_hook(pending)

        if response.is_filled:
            # The submit answer already carries the fill (today only the mock's INSTANT_FILL)
            filled = self.mark_filled(
                broker_ref=response.broker_ref,
                fill_price=response.fill_price,
                filled_lots=response.filled_lots,
            )
            if filled is None:
                return
            if item.action == PendingOrderAction.OPEN:
                if self._fill_open_hook is not None:
                    self._fill_open_hook(filled, response.fill_price, response.fill_fee)
            elif item.action == PendingOrderAction.CLOSE:
                if self._fill_close_hook is not None:
                    self._fill_close_hook(filled, response.fill_price, response.fill_fee)
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
                f'drain_inbox: EditResponse for {item.order_id} '
                f'but no modify_response_hook registered'
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
                f'drain_inbox: CancelResponse for {item.order_id} '
                f'but no cancel_response_hook registered'
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
                f'drain_inbox: PositionModifyResponse for {item.position_id} '
                f'but no position_modify_response_hook registered'
            )

    def _handle_query_response(self, item: QueryResponse) -> None:
        """
        Main-thread handler for a QueryResponse (#320).

        Delegates to the executor's _query_response_hook. The hook ALWAYS
        clears pending.execution_state.in_flight_query (the dispatched query is resolved
        either way), then applies the stale-broker_ref guard before any
        state mutation. Branches on broker status: FILLED → fill, terminal
        → rejection, PENDING/PARTIALLY_FILLED → no state change.
        """
        if self._query_response_hook is not None:
            self._query_response_hook(item)
        else:
            self.logger.warning(
                f'drain_inbox: QueryResponse for {item.order_id} '
                f'but no query_response_hook registered'
            )

    def _handle_trades_query_response(self, item: TradesQueryResponse) -> None:
        """
        Main-thread handler for a TradesQueryResponse (#326).

        Delegates to the executor's _trades_response_hook, which hands the
        executions to its registered consumers — today the drift auditor. It
        books nothing: a fill is booked once, from the answer that reported
        it (#508).
        """
        if self._trades_response_hook is not None:
            self._trades_response_hook(item)
        else:
            self.logger.warning(
                f'drain_inbox: TradesQueryResponse for {item.order_id} '
                f'but no trades_response_hook registered'
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
        _resting_response_hook for that storage to be updated.

        Args:
            order_id: Internal order identifier
            symbol, direction, lots, order_type: Order parameters
            adapter: Live-capable adapter
            **kwargs: price, stop_loss, take_profit, comment, expected_price
        """
        payload = adapter.build_submit_payload(
            symbol=symbol,
            direction=direction,
            lots=lots,
            order_type=order_type,
            **kwargs,
        )
        self._send_or_hold(SubmitJob(
            order_id=order_id,
            action=PendingOrderAction.OPEN,
            order_type=order_type,
            payload=payload,
            adapter=adapter,
            direction=direction,
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
        payload = adapter.build_submit_payload(
            symbol=symbol,
            direction=close_direction,
            lots=close_lots,
            order_type=OrderType.MARKET,
            **kwargs,
        )
        self._send_or_hold(SubmitJob(
            order_id=position_id,
            action=PendingOrderAction.CLOSE,
            order_type=OrderType.MARKET,
            payload=payload,
            adapter=adapter,
            direction=close_direction,
        ))

    def _send_or_hold(self, job: SubmitJob) -> None:
        """
        Hand a submit to the worker, or keep it back once the session is over (#642).

        Args:
            job: The submit
        """
        if self._holding_submits:
            self._held_submits.append(job)
            return
        self._http_outbox.put(job)

    def hold_new_submits(self) -> None:
        """
        Keep every submit from now on away from the worker, so none reaches the venue (#642).

        Set once the session is over and its last decision event is being delivered: an order a
        strategy sends from that hook is never sent, and take_unsent_submits hands it back.
        """
        self._holding_submits = True

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
        order_type: OrderType,
        new_price: Optional[float],
        new_stop_loss: Optional[float],
        new_take_profit: Optional[float],
        adapter: AbstractAdapter,
        new_limit_price: Optional[float] = None,
    ) -> None:
        """
        Enqueue an EditJob for the worker thread.

        Caller (LiveTradeExecutor.modify_limit_order / modify_stop_order) MUST
        already have:
          1. Resolved order_id → broker_ref via _active_*_orders lookup
          2. Set pending.execution_state.in_flight_operation = PENDING_MODIFY on the target
          3. Stored pending.execution_state.pending_modification = ModificationRequest(...)
          4. Validated that the order is not busy and not unconfirmed

        The worker dispatches the modify via adapter Tier-3 layers; the
        response arrives via _http_inbox and routes to _modify_response_hook
        on the next drain_inbox.

        Args:
            order_id: Internal order id (matches in_flight target)
            broker_ref: Current broker ref (some brokers return a NEW ref;
                        the hook handles update_broker_ref)
            symbol: Trading symbol (required by some adapters on modify)
            order_type: The type being amended — decides what new_price means at the venue
            new_price, new_stop_loss, new_take_profit: New values (None = no change)
            adapter: Live-capable adapter
            new_limit_price: New limit price of a STOP_LIMIT (None = no change)
        """
        self._http_outbox.put(EditJob(
            order_id=order_id,
            broker_ref=broker_ref,
            symbol=symbol,
            order_type=order_type,
            new_price=new_price,
            new_limit_price=new_limit_price,
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
        side (positions don't have a PendingOrder.execution_state.in_flight_operation flag
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
        market: Optional[TickData] = None,
    ) -> None:
        """
        Enqueue a QueryJob for the worker thread (#320).

        Triggered by LiveTradeExecutor._process_active_orders for every resting
        order, and by _process_pipeline_orders for every MARKET order and close,
        whose throttle window has elapsed and whose in_flight_query is False.
        The caller is responsible for setting in_flight_query = True
        and updating last_polled_at_ms BEFORE invoking this method — that keeps
        the scheduler state visible to subsequent throttle checks even before
        the job hits the worker.

        Args:
            order_id: Internal order identifier (primary routing key)
            broker_ref: Broker order reference at dispatch time. May be stale
                        by response time (an amend can re-mint refs) — the
                        executor's _handle_query_response applies the
                        stale-broker_ref guard.
            adapter: Live-capable adapter
            market: The quote at the moment the poll was decided, stamped here on the MAIN
                thread rather than read later in the worker — the same reason `ts_init` is
                a receipt stamp. DRY-RUN only (#505)
        """
        self._http_outbox.put(QueryJob(
            order_id=order_id,
            broker_ref=broker_ref,
            adapter=adapter,
            market=market,
        ))

    def submit_order_resolve_async(
        self,
        order_id: str,
        client_order_id: Optional[str],
        broker_ref: Optional[str],
        adapter: AbstractAdapter,
        range_start: datetime,
        range_end: datetime,
    ) -> None:
        """
        Enqueue an OrderResolveJob for the worker thread (#487).

        Triggered by LiveTradeExecutor's resolution scheduler for an order whose write was
        never answered. The caller owns the scheduling state — this method only hands the
        question to the worker, exactly as the poll path does.

        Args:
            order_id: Internal order identifier (primary routing key)
            client_order_id: The wire key the order was sent under, or None
            broker_ref: The venue reference, when one arrived before the answer was lost
            adapter: Live-capable adapter
            range_start: Start of the closed-order window to read (UTC)
            range_end: End of that window (UTC)
        """
        self._http_outbox.put(OrderResolveJob(
            order_id=order_id,
            client_order_id=client_order_id,
            broker_ref=broker_ref,
            adapter=adapter,
            range_start=range_start,
            range_end=range_end,
        ))

    def submit_trades_query_async(
        self,
        order_id: str,
        broker_ref: str,
        adapter: AbstractAdapter,
    ) -> None:
        """
        Enqueue a TradesQueryJob for the worker thread (#326).

        Triggered by the drift auditor once a fill is booked — an entry through
        the order-outcome listener, a close through the close-fill listener
        (#508). The worker fetches per-execution trade records via the
        adapter's Tier-3 trades_query layer; drain_inbox routes the response to
        _handle_trades_response, which hands it to the registered consumers.

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
        order_type: OrderType,
        new_price: Optional[float],
        new_stop_loss: Optional[float],
        new_take_profit: Optional[float],
        adapter: AbstractAdapter,
        new_limit_price: Optional[float] = None,
    ) -> BrokerResponse:
        """
        Synchronously modify an order via the adapter's Tier-3 layers.

        Composes adapter.build_modify_payload → do_request_modify →
        parse_modify_response. Blocks the caller thread for one broker
        roundtrip. Async modify (queued through the worker) is the subject
        of #318 and uses the same Tier-3 surface.

        The executor calls this so the Tier-3 layer-composition for modify
        stays encapsulated in the processor (parallel to submit / cancel).

        Args:
            broker_ref: Current broker order reference
            symbol: Trading symbol (some brokers require it on modify)
            order_type: The type being amended — decides what new_price means
            new_price: New limit price, or the new TRIGGER of a triggered type
                (None=no change)
            new_stop_loss: New stop loss (None=no change)
            new_take_profit: New take profit (None=no change)
            adapter: Live-capable adapter
            new_limit_price: New limit price of a STOP_LIMIT (None=no change)

        Returns:
            BrokerResponse (UNRESOLVED on a transport fault or an unreadable answer, REJECTED
            on the venue's refusal, PENDING on success,
            with a new broker_ref only from a venue that re-mints it)
        """
        payload = adapter.build_modify_payload(
            broker_ref=broker_ref,
            symbol=symbol,
            order_type=order_type,
            new_price=new_price,
            new_limit_price=new_limit_price,
            new_stop_loss=new_stop_loss,
            new_take_profit=new_take_profit,
        )
        # The call and the reading of its answer are told apart (#669)
        return self._write_answer(
            'modify', broker_ref,
            call=lambda _: adapter.do_request_modify(payload),
            parse=lambda raw, received: adapter.parse_modify_response(
                raw, original_broker_ref=broker_ref, ts_init=received),
        )

    def query_order_sync(
        self,
        broker_ref: str,
        adapter: AbstractAdapter,
        market: Optional[TickData] = None,
    ) -> BrokerResponse:
        """
        Synchronously query an order's current status via the adapter's Tier-3 layers.

        Composes adapter.build_query_payload → do_request_query →
        parse_query_response. Blocks the caller thread for one broker
        roundtrip. Used by LiveTradeExecutor for the Phase-1 poll inside
        _process_pending_orders and for the question the fill timeout asks
        before it gives an order up, and by the cold start.

        Args:
            broker_ref: Broker order reference to query
            adapter: Live-capable adapter
            market: The quote at the moment this poll was decided. DRY-RUN only — the
                simulated venue needs it to answer whether the market reached the order's
                price; a real broker knows its own book (#505)

        Returns:
            BrokerResponse with current status (UNRESOLVED when the query failed)
        """
        payload = adapter.build_query_payload(broker_ref)
        try:
            raw = adapter.do_request_query(payload)
            # Read inside the guard: an answer that cannot be read is a failed read, never an
            # exception in the caller's pass — the fill timeout asks through here (#669)
            return adapter.parse_query_response(
                raw, broker_ref, datetime.now(timezone.utc), market=market)
        except Exception as e:
            return self._failure_response(
                e, broker_ref=broker_ref, ts_init=datetime.now(timezone.utc),
                operation='status query', self_healing=True, is_read=True)

    def cancel_order_sync(
        self,
        broker_ref: str,
        adapter: AbstractAdapter,
    ) -> BrokerResponse:
        """
        Synchronously cancel an order via the adapter's Tier-3 layers.

        Composes adapter.build_cancel_payload → do_request_cancel →
        parse_cancel_response. Blocks the caller thread for one broker
        roundtrip. Async cancel (queued through the worker) is the subject
        of #318 and uses the same Tier-3 surface.

        Args:
            broker_ref: Broker order reference to cancel
            adapter: Live-capable adapter

        Returns:
            BrokerResponse (UNRESOLVED on a transport fault or an unreadable answer, REJECTED
            on the venue's refusal, CANCELLED on success)
        """
        payload = adapter.build_cancel_payload(broker_ref)
        # The call and the reading of its answer are told apart (#669): the fill timeout and a
        # stop's give-up cancel through here, on the loop's own thread
        return self._write_answer(
            'cancel', broker_ref,
            call=lambda _: adapter.do_request_cancel(payload),
            parse=lambda raw, received: adapter.parse_cancel_response(raw, broker_ref, received),
        )
