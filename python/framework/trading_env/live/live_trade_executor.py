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
    DeferredClose,
    TimeoutConfig,
)
from python.framework.types.live_types.live_request_types import QueryResponse, TradesQueryResponse
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason,
    EntryType,
)
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
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

# Lot tolerance for the venue-held close resolver (#503). Volumes cross the wire as
# decimal strings and come back as floats, so "the same volume again" is never bit-equal.
# Below every venue's minimum increment by orders of magnitude, so it can only ever
# absorb float noise, never a real fill.
_VENUE_CLOSE_LOT_EPSILON = 1e-9


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
        venue_held_protection: bool = False,
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
            venue_held_protection: The profile's default for #503 — whether a declared
                stop_loss additionally rests at the venue as an order of its own. An
                order may override it; the adapter's capability can refuse it.
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
        # DRY-RUN only: the simulated venue could not decide this order's state (#505). Also a
        # standing condition — the next poll produces the same non-answer — and also said once.
        self._reported_undecided: Set[str] = set()
        # #503 — the profile's default for venue-held protection. An order may override it
        # (OpenOrderRequest.venue_held_protection); the adapter's capability may refuse it.
        self._venue_held_protection_default = venue_held_protection
        # #503 — closes waiting for the venue to confirm the protective order is gone.
        # Keyed by position, because that is what a close names and what a protective
        # order protects; at most one close is in flight for a position at a time.
        self._deferred_closes: Dict[str, DeferredClose] = {}
        # Positions that lost their protective order to a PARTIAL close and must get a
        # fresh one at the remaining size once that close resolves (#503).
        self._reinstate_after_close: Set[str] = set()
        # #503 — venue closes the BOOT discovered, waiting for the first tick. A close
        # cannot be booked before one exists: the trade record needs a bid and an ask,
        # and `get_current_price` raises without them. So the boot records what the venue
        # did and the first tick books it.
        self._boot_venue_closes: List[Tuple[str, float, Optional[float]]] = []
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
        Internal ids whose submit answer has not come back yet (#355).

        The truth pull joins on `broker_ref`, so anything we have sent and not yet heard
        about has no counterpart to join to — and would read as an order we placed and
        stopped tracking. It is tracked; its reference simply has not arrived.

        TWO sources, and the second was missing (measured 2026-09-10, twice in five field
        study runs). A MARKET or CLOSE order in flight lives in the processor's world, which
        the resting-order pull does not compare against at all. But a RESTING order inside
        its OWN submit round trip is equally unjoinable: it sits in the active list with
        `broker_ref=None`, so the venue already reports it under a key we cannot match. Both
        occurrences were three limits submitted back to back with a reconcile tick landing
        in the two seconds before the references came back; each resolved cleanly moments
        later, and each had produced an ERROR saying the order was forgotten.

        Suppressing it loses no coverage: an answer that never arrives is reported by its
        own channel (#473 — `_unresolved_report_after_s`, once per order), which is a
        statement about OUR transport rather than about the venue's books.

        Returns:
            The pending order ids the processor holds, plus every resting order still
            waiting for its broker reference
        """
        in_flight = {p.pending_order_id
                     for p in self._request_processor.get_pending_orders()}
        in_flight.update(
            p.pending_order_id
            for p in self._active_limit_orders + self._active_stop_orders
            if p.broker_ref is None
        )
        return in_flight

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
            if pending.closes_position_id:
                # #503 — this is the THIRD place a broker_ref goes from None to set, and a
                # protective order owes its position the stamp wherever that happens. The
                # venue is holding this stop; without the stamp the position still reads
                # LOCAL, so our own check closes it too and both fire. The carry-over would
                # then record no reference either, and the next boot could never cancel it.
                self._stamp_protective_confirmation(pending)

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
                    pending.broker_ref, self.broker.adapter,
                    market=self._current_tick)
                self._handle_broker_response(pending, response)

            # Check for timeouts (orders that broker never responded to)
            timed_out = self._request_processor.check_timeouts()
            for pending in timed_out:
                self._handle_timeout(pending)

        # === Phase 2: Active limit/stop orders (broker-side, waiting for trigger) ===
        self._process_active_orders()

    def _report_undecided_poll(
        self,
        order_id: str,
        broker_ref: Optional[str],
        response: BrokerResponse,
    ) -> None:
        """
        Say that a DRY-RUN poll produced no decision, once per order (#505).

        The dry-run simulator plays the venue, and a venue that cannot answer must not be
        read as one that answered "still working". It can only happen in dry-run: a real
        broker either knows the state or is unreachable, and the second case is UNRESOLVED.

        Why the executor and not the simulator: the adapter has no logger by design — it is
        a transport — so the refusal travels on the response and is made visible here, in the
        SESSION channel, which is what reaches the error pot and the run summary (§35).

        Said once, like its unknown-order sibling: the next poll produces the same
        non-answer, and repeating it every cycle would bury the channel it has to reach.

        Args:
            order_id: Internal order identifier
            broker_ref: The reference that was polled
            response: The response carrying `undecided_reason`
        """
        if order_id in self._reported_undecided:
            return
        self._reported_undecided.add(order_id)
        self.logger.error(
            f'🎭 Dry-run cannot decide order {order_id} (broker_ref={broker_ref}): '
            f'{response.undecided_reason}. It is never booked as a FILL — a rehearsal that '
            f'invents one is worse than no rehearsal — so this session is NOT exercising '
            f'this order path. A MARKET order in this state is still cancelled and recorded '
            f'by the submit timeout; a resting one simply waits, because a resting order does '
            f'not expire.')

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
        if response.undecided_reason:
            self._report_undecided_poll(
                pending.pending_order_id, pending.broker_ref, response)
            return

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
            self._route_resting_fill(
                filled,
                fill_price=response.fill_price,
                filled_lots=response.filled_lots,
            )

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
            if pending.closes_position_id:
                # #503 — the operator asked for a level the venue would hold and did not
                # get one. The position is still open and still protected only by this
                # process, which is precisely the state a 30-day run must not enter
                # unnoticed (§35: the session channel, not global.log).
                self._clear_protective_stamp(pending)
                self.logger.error(
                    f'❌ The venue REFUSED the protective STOP for '
                    f'{pending.closes_position_id}: '
                    f"{response.rejection_reason or 'unknown'}. The position is open and "
                    f'its stop is enforced by THIS PROCESS ONLY — it does not survive a '
                    f'restart.')
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
        if pending.closes_position_id and not response.is_filled:
            # #503 — ONLY NOW does the venue hold it, and only now does the local check
            # step aside for this position's stop. Not on the declaration, not on the
            # submit: in between, nobody at the venue holds anything.
            self._stamp_protective_confirmation(pending)

        if response.is_filled:
            # Sync-fill (rare — e.g. price already crossed at submit).
            # FILLED-precedence: a fill wins over any deferred cancel (#361).
            self._drop_active_order(pending)
            self._route_resting_fill(
                pending,
                fill_price=response.fill_price,
                filled_lots=response.filled_lots,
            )
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

    def _stamp_protective_confirmation(self, protective: PendingOrder) -> None:
        """
        Record the venue's reference on the position the protective order protects (#503).

        The stamp is what `Position.protective_enforcement` derives VENUE from, so it is
        the single moment the local stop check stands down for that position — and it is
        deliberately driven by the venue's answer rather than by our request.

        Args:
            protective: The confirmed protective order
        """
        position = self.portfolio.get_position(protective.closes_position_id)
        if position is None:
            self.logger.error(
                f'❌ The venue confirmed protective order {protective.broker_ref} for '
                f'{protective.closes_position_id}, which this bot no longer holds. It is '
                f'resting over a position that is gone — cancel it by hand.')
            return
        position.protective_broker_ref = protective.broker_ref
        self.logger.info(
            f'🛡️ The venue now holds the stop for {position.position_id} '
            f'(broker_ref={protective.broker_ref}) — it survives this process')

    def _clear_protective_stamp(self, protective: PendingOrder) -> None:
        """
        Hand the position's stop back to the local check (#503).

        Called wherever a protective order stops existing at the venue. Clearing the
        reference is what makes `protective_enforcement` answer LOCAL again, so the tick
        check resumes on the very next tick rather than leaving the level with nobody.

        Args:
            protective: The protective order that is no longer at the venue
        """
        position = self.portfolio.get_position(protective.closes_position_id)
        if position is None:
            return
        position.protective_broker_ref = None
        position.protective_order_id = None

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
            if pending.closes_position_id:
                # #503 — a refused amend of a PROTECTIVE order must not feed the
                # OrderGuard cooldown. Measured on the trailing stop: 378 amends over 62
                # positions, worst burst 20 in 39 ticks, and two rejections block EVERY
                # new order in that direction for 60 s — the replacement protective order
                # included. The refusal stays visible in the log and the order history; it
                # simply is not a new-order rejection, because no new order was attempted.
                self.logger.error(
                    f'❌ The venue refused an amend of the protective order for '
                    f'{pending.closes_position_id}: '
                    f"{response.rejection_reason or 'unknown'}")
            else:
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
                if pending.closes_position_id:
                    # #503 — a re-minted reference has to reach the position too, or the
                    # carry-over records a txid the venue has retired and the next boot
                    # reads its own protection as an ABSENCE. Cannot fire on Kraken, whose
                    # AmendOrder keeps the txid (§32); it is the SECOND adapter that re-mints
                    # — #209, where a green suite proves nothing about this line.
                    self._stamp_protective_confirmation(pending)

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
            if pending.closes_position_id:
                self._abandon_deferred_close(
                    pending.closes_position_id,
                    f"the venue refused to cancel the protective order "
                    f"({response.rejection_reason or 'unknown'})")
            pending.execution_state.in_flight_operation = PendingOperation.NONE
            return

        # Success — remove from active list. No order_history append: the
        # algo's discipline pattern (has_pending_orders / has_in_flight_operation)
        # observes the state transition naturally. Order_history is reserved
        # for EXECUTED / REJECTED-by-broker, not for algo-initiated cancels.
        self._drop_active_order(pending)
        if pending.closes_position_id:
            # #503 — the venue no longer holds this position's stop, so the local check
            # has to take it back on the very next tick. Leaving the stamp would leave
            # the level with nobody: silent here, and enforced by neither.
            self._clear_protective_stamp(pending)

        pending.execution_state.in_flight_operation = PendingOperation.NONE
        self.logger.info(
            f'❌ Order {order_id} cancel resolved (broker_ref={pending.broker_ref})'
        )
        self._emit_order_cancelled(pending)
        if pending.closes_position_id:
            # #503 — and THIS is what a close was waiting for. Released after the line
            # above so the log reads in the order the events happened; a reader tracing a
            # cancel-then-close sequence should not have to reconstruct it.
            self._release_deferred_close(pending.closes_position_id)

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
                self._route_resting_fill(
                    pending,
                    fill_price=pending.fills.cumulative_avg_price,
                    entry_type=entry_type,
                    fill_type=FillType.LIMIT,
                    filled_lots=pending.fills.cumulative_filled_lots,
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
                # Stamped here, on the main thread, where the tick gate above already
                # guarantees one. In dry-run this is what lets the simulated venue answer
                # whether the market reached this order's price (#505).
                market=self._current_tick,
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

        if broker_response.undecided_reason:
            self._report_undecided_poll(order_id, pending.broker_ref, broker_response)
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

        # #503 — a protective order the venue holds is fed to the resolver on THREE
        # answers, not only on FILLED. `vol_exec` reaches us on every status (#505), and
        # each of the three represents real lots leaving the position.
        venue_held = bool(pending.closes_position_id)
        venue_filled = broker_response.filled_lots or 0.0

        if broker_response.status == BrokerOrderStatus.FILLED:
            entry_type, fill_type = self._resting_fill_classification(pending)
            self._drop_active_order(pending)
            self._route_resting_fill(
                pending,
                fill_price=broker_response.fill_price,
                entry_type=entry_type,
                fill_type=fill_type,
                filled_lots=broker_response.filled_lots,
            )
            self.logger.info(
                f'🎯 Active {entry_type.value} order {order_id} filled at '
                f'{broker_response.fill_price} (broker_ref={pending.broker_ref})'
            )
        elif broker_response.is_terminal and venue_held and venue_filled > 0:
            # The dangerous shape: the venue took PART of a protective order and then the
            # order ended (cancelled, expired). Booked as a plain broker rejection those
            # lots would be lost from our books while they are gone from the account.
            self._drop_active_order(pending)
            self._apply_venue_close(
                pending,
                filled_lots=venue_filled,
                avg_price=broker_response.fill_price,
            )
            self._clear_protective_stamp(pending)
            self.logger.warning(
                f'🛑 Protective order {order_id} ended as '
                f'{broker_response.status.value} after closing {venue_filled} lots '
                f'(broker_ref={pending.broker_ref}) — the executed part is booked, and '
                f'any remaining position is back under the local check')
        elif broker_response.is_terminal:
            # REJECTED / CANCELLED / EXPIRED by broker
            self._drop_active_order(pending)
            if pending.closes_position_id:
                # #503 — it ended without executing anything, so the position it was
                # protecting is unprotected from here. Give it back to the local check.
                self._clear_protective_stamp(pending)
                self.logger.error(
                    f'❌ The protective STOP for {pending.closes_position_id} ended as '
                    f'{broker_response.status.value} at the venue without filling. The '
                    f'position is open and its stop is enforced by THIS PROCESS ONLY.')
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
        elif venue_held and venue_filled > 0:
            # A partial fill on a still-open protective order. Kraken has no
            # PARTIALLY_FILLED — a half-filled order stays `open` and reports what
            # executed beside it. The order STAYS active and is re-polled for the rest;
            # what already closed is booked now, because it is real money out of the
            # position and the resolver is idempotent about seeing it again.
            self._apply_venue_close(
                pending,
                filled_lots=venue_filled,
                avg_price=broker_response.fill_price,
            )
        # else: PENDING / PARTIALLY_FILLED — no state change, next cycle re-polls

    # ============================================
    # Order Submission (live-specific)
    # ============================================

    def _fill_open_order(
        self,
        pending_order: PendingOrder,
        fill_price: Optional[float] = None,
        entry_type: EntryType = EntryType.MARKET,
        fill_type: FillType = FillType.MARKET
    ) -> None:
        """
        Book the entry, then give the venue the position's stop to hold (#503).

        The placement hangs off the FILL and not off the submit, because there is nothing
        to protect until a position exists — and it hangs off this one method because
        every route into a live position passes through it.

        Args:
            pending_order: The order the venue reported filled
            fill_price: The price it filled at
            entry_type: MARKET or LIMIT — how the position was entered
            fill_type: How the fill came about
        """
        super()._fill_open_order(
            pending_order,
            fill_price=fill_price,
            entry_type=entry_type,
            fill_type=fill_type,
        )
        if not pending_order.venue_held_protection:
            return
        position = self.portfolio.get_position(pending_order.pending_order_id)
        if position is None or position.stop_loss is None:
            return
        self._place_protective_order(position)

    def _fill_close_order(
        self,
        pending_order: PendingOrder,
        fill_price: Optional[float] = None,
        close_reason: CloseReason = CloseReason.MANUAL,
        exit_trades: Optional[List[BrokerTrade]] = None
    ) -> float:
        """
        Book the close, then re-protect whatever the position has left (#503).

        A PARTIAL close leaves a position behind at a smaller size, and its protective
        order was cancelled to let the close through safely (see `close_position`). The
        remainder would otherwise sit at the venue with no stop over it — protected only
        by this process, silently, from a routine partial close.

        Kraken's AmendOrder can change a volume; our modify path cannot, so a fresh order
        is the honest route rather than a half-built one.

        Args:
            pending_order: The close order the venue confirmed
            fill_price: The price it filled at
            close_reason: Why the position was closed
            exit_trades: The executions to record — see the base method

        Returns:
            The lots actually booked
        """
        booked = super()._fill_close_order(
            pending_order,
            fill_price=fill_price,
            close_reason=close_reason,
            exit_trades=exit_trades,
        )
        position_id = pending_order.closes_position_id or pending_order.pending_order_id
        if position_id not in self._reinstate_after_close:
            return booked
        self._reinstate_after_close.discard(position_id)
        position = self.portfolio.get_position(position_id)
        if position is None or position.stop_loss is None:
            # A full close after all, or the level was withdrawn on the way. Nothing left
            # to protect, and inventing an order for it would be worse than none.
            return booked
        self.logger.info(
            f'🛡️ {position_id} survived the close with {position.lots} lots — placing a '
            f'fresh protective order at its remaining size')
        self._place_protective_order(position)
        return booked

    def on_tick(self, tick: TickData) -> None:
        """
        Book what the venue did while nothing was running, then run the normal tick.

        A close cannot be booked before a tick exists — the trade record needs a bid and
        an ask — so the boot only RECORDS what the venue reported about a carried
        protective order, and the first tick is where it becomes a trade (#503).

        Args:
            tick: The tick that is arriving
        """
        super().on_tick(tick)
        # AFTER the base pass, which is what sets the tick, the prices and the canonical
        # clock — all three are needed to write a trade record. The local SL/TP check
        # runs first and correctly leaves these positions alone: they still carry the
        # venue stamp, so it stands aside for them.
        if self._boot_venue_closes:
            self._drain_boot_venue_closes()

    def _drain_boot_venue_closes(self) -> None:
        """Book the closes the boot found, now that there is a price to record them against."""
        pending = self._boot_venue_closes
        self._boot_venue_closes = []
        for position_id, filled_lots, fill_price in pending:
            position = self.portfolio.get_position(position_id)
            if position is None:
                continue
            protective = PendingOrder(
                pending_order_id=(position.protective_order_id
                                  or f'protect_{position_id}'),
                order_action=PendingOrderAction.CLOSE,
                order_type=OrderType.STOP,
                broker_ref=position.protective_broker_ref,
                symbol=position.symbol,
                direction=(OrderDirection.SHORT
                           if position.direction == OrderDirection.LONG
                           else OrderDirection.LONG),
                close_reason=CloseReason.SL_TRIGGERED,
                closes_position_id=position_id,
            )
            self._apply_venue_close(
                protective, filled_lots=filled_lots, avg_price=fill_price)

    def get_request_processor(self) -> LiveRequestProcessor:
        """
        The live request processor, for a caller that must compose a Tier-3 round trip
        itself — today only the cold-start boot, which has to ASK the venue about a
        carried protective order before this session has a tick or a loop (#503).

        Returns:
            The processor this executor submits through
        """
        return self._request_processor

    def apply_carried_venue_close(
        self,
        position: Position,
        filled_lots: float,
        fill_price: Optional[float],
    ) -> None:
        """
        Book a close the venue performed while nothing was running (#503).

        The boot's counterpart to the in-session resolver: a protective order that fired
        overnight already moved the money, and the books have to catch up or every number
        after it is wrong — the balance, the P&L, and the next cross-check.

        Args:
            position: The restored position the venue closed
            filled_lots: The volume the venue reports it executed
            fill_price: The price it executed at
        """
        self.logger.info(
            f'🛡️ Cold start: the venue closed {position.position_id} through its '
            f'protective order while nothing was running — booking it on the first tick')
        self._boot_venue_closes.append(
            (position.position_id, filled_lots, fill_price))

    def adopt_carried_protective_order(self, position: Position) -> None:
        """
        Take a still-resting protective order back into this session's stop world (#503).

        Without this the order rests at the venue and no session knows it: it cannot be
        amended when the level moves, cannot be cancelled before a close, and reappears
        at the next boot as somebody else's order.

        Args:
            position: The restored position whose protective order is still resting
        """
        order_id = position.protective_order_id or f'protect_{position.position_id}'
        protective = PendingOrder(
            pending_order_id=order_id,
            order_action=PendingOrderAction.CLOSE,
            order_type=OrderType.STOP,
            broker_ref=position.protective_broker_ref,
            symbol=position.symbol,
            direction=(OrderDirection.SHORT if position.direction == OrderDirection.LONG
                       else OrderDirection.LONG),
            entry_price=position.stop_loss,
            close_lots=position.lots,
            close_reason=CloseReason.SL_TRIGGERED,
            closes_position_id=position.position_id,
            order_kwargs={'stop_price': position.stop_loss},
        )
        self._active_stop_orders.append(protective)
        position.protective_order_id = order_id
        self.logger.info(
            f'🛡️ Cold start: the venue is still holding the stop for '
            f'{position.position_id} (broker_ref={position.protective_broker_ref}) — '
            f'adopted, and the local check stays aside for it')

    def _place_protective_order(self, position: Position) -> None:
        """
        Send the position's stop to the venue as an order of its own (#503).

        It is minted from the ORDER counter like any other order — the wire key derives
        from that id, and overloading the position's id would collide with a restart's
        counter — so it names its position in `closes_position_id` instead of being it.
        The direction is REVERSED: what protects a LONG is a sell.

        The stamp that silences the local check is NOT set here. It is set when the venue
        CONFIRMS (see _handle_resting_submit_response): between sending and hearing back
        nobody at the venue holds anything, and a level watched by neither is the defect
        #500 exists to prevent.

        Args:
            position: The freshly opened position whose stop_loss should rest at the venue
        """
        # Minted from the SAME counter every order uses. The wire key is the trailing
        # number of this id (`build_client_order_id`), so a private counter is not merely
        # untidy — measured 2026-09-10, `protect_pos_ethusd_27_28` and the next entry
        # `pos_ethusd_28` produced the same key and Kraken refused the second with
        # `EGeneral:Invalid arguments:cl_ord_id not unique`. Consuming a counter also
        # keeps the cold-start high-water mark honest, so a restart cannot re-issue it.
        order_id = f'protect_{self.portfolio.get_next_position_id(position.symbol)}'
        direction = (OrderDirection.SHORT if position.direction == OrderDirection.LONG
                     else OrderDirection.LONG)
        protective = PendingOrder(
            pending_order_id=order_id,
            order_action=PendingOrderAction.CLOSE,
            order_type=OrderType.STOP,
            timing=PendingOrderTiming(submitted_at=datetime.now(timezone.utc)),
            broker_ref=None,
            symbol=position.symbol,
            direction=direction,
            # For a stop the resting price IS the trigger — the same convention the
            # entry side and the simulation use.
            entry_price=position.stop_loss,
            entry_time=self.get_current_time(),
            close_lots=position.lots,
            close_reason=CloseReason.SL_TRIGGERED,
            closes_position_id=position.position_id,
            order_kwargs={'stop_price': position.stop_loss},
            submission=self._current_submission(),
        )
        self._active_stop_orders.append(protective)
        position.protective_order_id = order_id
        self._orders_sent += 1
        self._request_processor.submit_open_order_async(
            order_id=order_id,
            symbol=position.symbol,
            direction=direction,
            lots=position.lots,
            order_type=OrderType.STOP,
            adapter=self.broker.adapter,
            client_order_id=self.build_client_order_id(order_id),
            stop_price=position.stop_loss,
        )
        self.logger.info(
            f'🛡️ Protective STOP submitted for {position.position_id} at '
            f'{position.stop_loss:.5f} ({direction.value} {position.lots}) — the local '
            f'check keeps watching until the venue confirms it')

    def _route_resting_fill(
        self,
        pending: PendingOrder,
        fill_price: Optional[float],
        entry_type: EntryType = EntryType.MARKET,
        fill_type: FillType = FillType.MARKET,
        filled_lots: Optional[float] = None,
    ) -> None:
        """
        Send a confirmed fill to the OPEN or the CLOSE half, by the order's own action.

        Three of the four fill sites called `_fill_open_order` unconditionally, which was
        true as long as every resting order was an entry. Since #503 a resting order can
        be a protective one the venue holds: routed the old way, a firing stop would OPEN
        a second position, book an entry fee and tell the algo an order had filled. The
        branch lives in ONE place rather than at each site (§19).

        Args:
            pending: The order the venue reported filled
            fill_price: The price it filled at
            entry_type: MARKET or LIMIT — how the position was entered (open side only)
            fill_type: How the fill came about (open side only)
            filled_lots: Volume the venue reports filled — read only by the venue-held
                protective route, where it is the resolver's delta input
        """
        if pending.order_action == PendingOrderAction.CLOSE:
            if pending.closes_position_id:
                self._apply_venue_close(
                    pending,
                    filled_lots=self._venue_close_volume(pending, filled_lots),
                    avg_price=fill_price,
                )
                return
            self._fill_close_order(pending, fill_price=fill_price)
            return
        self._fill_open_order(
            pending,
            fill_price=fill_price,
            entry_type=entry_type,
            fill_type=fill_type,
        )

    def _venue_close_volume(
        self,
        protective: PendingOrder,
        reported_lots: Optional[float],
    ) -> float:
        """
        How many lots a venue-held protective order has closed, when the answer said FILLED.

        The venue's own figure wins. Where the answer carried none, a FILLED protective
        order has by definition closed everything it was for — `close_lots` when it names a
        size, otherwise the position's whole remaining size, because a protective order
        with no size closes the position. Falling back to 0.0 instead would make the
        resolver book NOTHING and say nothing about it, which §35 forbids outright: a
        stop-out that leaves no trace is indistinguishable from a stop that never fired.

        Args:
            protective: The protective order the venue reported filled
            reported_lots: The volume on the venue's answer, or None

        Returns:
            The volume to book, or 0.0 only when nothing can be determined — and then an
            error naming the reference has already been written
        """
        if reported_lots is not None and reported_lots > 0:
            return reported_lots
        if protective.close_lots:
            return protective.close_lots
        position = (self.portfolio.get_position(protective.closes_position_id)
                    if protective.closes_position_id else None)
        if position is not None:
            return position.lots
        self.logger.error(
            f'❌ Protective order {protective.broker_ref} came back FILLED with no volume '
            f'and no position to size it against ({protective.closes_position_id}). '
            f'Nothing was booked. Check the account against that reference by hand.')
        return 0.0

    def _apply_venue_close(
        self,
        protective: PendingOrder,
        filled_lots: float,
        avg_price: Optional[float],
    ) -> None:
        """
        Book what the VENUE closed through a protective order it held (#503).

        Idempotent by construction, because it has to be: the venue reports the same fill
        through several routes — a FILLED query, a terminal answer that still carries
        volume, a partial one that does not, and after a restart the boot resolver. It
        applies the DELTA against what is already written into the position book, and
        that counter is its own (`venue_close_applied_lots`, decision 13.a): the fills
        aggregate answers how much the VENUE filled, and on a venue with trade-level
        reporting the trades drain sets it to the full amount before anything is booked.

        Two answers it must never give silently. A volume for a position this bot no
        longer holds is not attributable — it reaches the session pot naming the venue's
        reference, which is the only handle left for a manual check (§35). So does an
        excess beyond what the position still holds.

        Args:
            protective: The protective order, naming its position in closes_position_id
            filled_lots: Total volume the venue reports filled on it so far
            avg_price: Volume-weighted price of that fill
        """
        state = protective.execution_state
        consumed = state.venue_close_applied_lots
        delta = filled_lots - consumed
        if delta <= _VENUE_CLOSE_LOT_EPSILON:
            # A second arrival of the same volume — a no-op, which is the whole point.
            return

        position_id = protective.closes_position_id
        position = (self.portfolio.get_position(position_id) if position_id else None)
        if position is None:
            state.venue_close_applied_lots = filled_lots
            self.logger.error(
                f'❌ Protective order {protective.broker_ref} closed {delta:.8f} lots '
                f'for position {position_id}, which this bot no longer holds. Nothing '
                f'was booked. Check the account against that reference by hand.')
            return

        bookable = min(delta, position.lots)
        if delta - bookable > _VENUE_CLOSE_LOT_EPSILON:
            self.logger.error(
                f'❌ Protective order {protective.broker_ref} reports {delta:.8f} lots '
                f'closed on position {position_id}, which holds {position.lots:.8f}. '
                f'The excess {delta - bookable:.8f} is unattributable and was not booked.')

        exit_trades = self._unbooked_close_trades(protective, consumed)
        protective.close_lots = bookable
        # Record what was BOOKED, never what was requested. `_fill_close_order` converts a
        # partial into a full close when the remainder would fall under volume_min, so it
        # can book MORE than asked — and a counter that under-records would make the
        # venue's next report of the same volume look like an unattributable excess and
        # raise a false alarm about a healthy close.
        booked = self._fill_close_order(
            protective,
            fill_price=avg_price,
            exit_trades=exit_trades,
        )
        state.venue_close_applied_lots = consumed + booked

    @staticmethod
    def _unbooked_close_trades(
        protective: PendingOrder,
        consumed_lots: float,
    ) -> Optional[List[BrokerTrade]]:
        """
        The executions of a protective order that are not in the position book yet.

        A protective order can be booked in two steps — a partial fill, then the rest —
        and `_fill_close_order` hands its trade list to the portfolio as the closing
        record's executions. Handing it the SAME list twice would report the first
        partial's executions on both closes. A trade is atomic, so a booking boundary
        always falls between two of them.

        Args:
            protective: The protective order
            consumed_lots: How much of it is already booked

        Returns:
            The unbooked executions, or None when there are none to hand over — then
            _fill_close_order synthesizes one, exactly as for every other close
        """
        if not protective.fills.trades:
            return None
        running = 0.0
        unbooked: List[BrokerTrade] = []
        for trade in protective.fills.trades:
            running += trade.volume
            if running > consumed_lots + _VENUE_CLOSE_LOT_EPSILON:
                unbooked.append(trade)
        # An EMPTY slice is returned as such, and it is NOT the same message as None.
        # None says "this order has no executions at all"; an empty list says "it has
        # some, and none of them belong to THIS booking" — which happens when the venue
        # reports more volume than its executions account for. `_fill_close_order`
        # synthesizes for the second case and would otherwise re-report the executions of
        # the previous booking, fee and all.
        return unbooked

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

    def resolve_venue_held_protection(self, request: OpenOrderRequest) -> bool:
        """
        Whether THIS order's protective level should rest at the venue (#503).

        Two declarations, and the order wins where it speaks: the profile states the
        session's intent, an order overrides it for itself. There is no third layer — a
        cascade would raise "which one wins" for a switch that moves real money.

        Args:
            request: The incoming order

        Returns:
            True when a protective order should be placed for the resulting position
        """
        if request.venue_held_protection is None:
            return self._venue_held_protection_default
        return request.venue_held_protection

    def _reject_if_venue_cannot_hold_protection(
        self,
        request: OpenOrderRequest,
        order_id: str
    ) -> Optional[OrderResult]:
        """
        Refuse an order that demands a venue-held protection this venue cannot carry.

        Live only, and deliberately so: the SIMULATION accepts the flag and changes
        nothing (it has no venue to place at, and enforces the level itself as before).
        Refusing there would make a strategy that opts in un-backtestable, and sim/live
        parity is the point of the project.

        The message names the SHORT side — the profile or the venue — because a refusal
        an operator cannot act on is only half a refusal; the pre-flight intersection
        already answers the same way.

        Args:
            request: The incoming order
            order_id: The id the rejection is recorded under

        Returns:
            The rejection for open_order() to return, or None when the order may proceed
        """
        if request.stop_loss is None:
            # Nothing to protect, so nothing to place — a profile-wide opt-in must not
            # turn every unprotected entry into a rejection.
            return None
        if not self.resolve_venue_held_protection(request):
            return None
        if self.broker.get_order_capabilities().venue_held_protective_orders:
            return None

        self._orders_rejected += 1
        result = create_rejection_result(
            order_id=order_id,
            reason=RejectionReason.ORDER_TYPE_NOT_SUPPORTED,
            message=(
                f'venue_held_protection was requested, but '
                f'{self.get_broker_name()} cannot hold a protective order for a '
                f'position. Turn it off in the profile (execution.venue_held_protection) '
                f'or pass venue_held_protection=False on this order.'),
        )
        self._check_order_history_limit()
        self._order_history.append(result)
        return result

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

        # #503 — the order asks for a level the venue holds; refuse before anything is
        # sent, rather than opening a position whose protection cannot follow it.
        protection_rejection = self._reject_if_venue_cannot_hold_protection(
            request, order_id)
        if protection_rejection:
            return protection_rejection

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

        # #503 — resolved ONCE here and carried on the pending, because the fill site sees
        # the order and not the request that made it. The refusal above has already run,
        # so a True here means the venue can carry it.
        venue_held_protection = self.resolve_venue_held_protection(request)

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
                venue_held_protection=venue_held_protection,
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
            venue_held_protection=venue_held_protection,
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

        # #503 — cancel BEFORE closing, never beside it. At spot there is no reduce_only,
        # and a market close goes through — measured — while a protective stop still rests
        # over the same holding: both can fill, and the second sells coins that are gone.
        # So the close is held until the venue confirms the cancel. Failing to confirm
        # leaves the position OPEN and PROTECTED, which is the safe end of the two.
        #
        # Gated on the ORDER's existence, not on its confirmation. Measured 2026-09-10 in
        # a real field-study run: gating on `protective_broker_ref` left the window
        # between submitting the protective order and hearing back wide open — the close
        # went out at 15:37:14, the venue confirmed the stop at 15:37:18, and the order
        # outlived the position it protected as an orphan at the venue. `_cancel_resting_
        # order` handles an unconfirmed order through the deferred-cancel path (#361).
        #
        # A close that is ALREADY waiting must not be overtaken by the next request, and
        # the next request is the ordinary case rather than the exotic one: a deferred
        # close registers nothing with the request processor, so `is_pending_close` and
        # `has_pending_orders` both stay False, and the framework's own SL/TP check calls
        # in again on every tick for as long as the level is breached. Letting the second
        # request through would send the close beside the still-resting stop — the exact
        # double-fill the deferral exists to prevent, reached by the most normal sequence
        # there is.
        if position_id in self._deferred_closes:
            self.logger.debug(
                f'🛡️ Close of {position_id} is already waiting for the protective order '
                f'to be cancelled — the repeat request joins it rather than racing it')
            return self._deferred_close_result(position)
        if position.protective_order_id:
            return self._defer_close_behind_cancel(position, lots, close_reason)

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

    def _defer_close_behind_cancel(
        self,
        position: Position,
        lots: Optional[float],
        close_reason: CloseReason,
    ) -> OrderResult:
        """
        Cancel the venue's protective order first and hold the close until it is gone.

        The order of these two is not a preference. A close that races the protective
        order can be filled twice — once by us, once by the venue — and at spot the
        second fill sells a holding that no longer exists.

        Args:
            position: The position being closed
            lots: Lots to close, or None for all
            close_reason: Why the close was requested

        Returns:
            PENDING while the cancel is in flight, or a rejection when it cannot be sent
        """
        order_id = position.protective_order_id
        scheduled = bool(order_id) and self._cancel_resting_order(
            order_id, self._active_stop_orders, 'protective stop')
        if not scheduled:
            self.logger.error(
                f'❌ The close of {position.position_id} was NOT sent: its protective '
                f'order {order_id} could not be cancelled first, and closing beside a '
                f'live stop can fill twice. The position stays open and protected.')
            return create_rejection_result(
                order_id=f'close_{position.position_id}',
                reason=RejectionReason.BROKER_ERROR,
                message=('protective order could not be cancelled first — close withheld '
                         'rather than raced against it'),
            )

        # A partial close leaves a position behind, and that remainder needs protecting
        # again at its new size. Kraken can amend a volume; our modify path cannot, so
        # the honest route is a fresh order once the close has resolved.
        is_partial = lots is not None and lots < position.lots
        self._deferred_closes[position.position_id] = DeferredClose(
            lots=lots, close_reason=close_reason, reinstate=is_partial)
        self.logger.info(
            f'🛡️ Close of {position.position_id} is waiting for the venue to confirm the '
            f'protective order is cancelled — it will be sent then')
        return self._deferred_close_result(position)

    def _deferred_close_result(self, position: Position) -> OrderResult:
        """
        What a caller hears while its close waits behind the protective order's cancel.

        Args:
            position: The position whose close is deferred

        Returns:
            A PENDING result naming the close rather than the position
        """
        return OrderResult(
            order_id=f'close_{position.position_id}',
            status=OrderStatus.PENDING,
            position_id=position.position_id,
            action=OrderAction.CLOSE,
            symbol=position.symbol,
            direction=position.direction,
            submission=self._current_submission(),
        )

    def _release_deferred_close(self, position_id: str) -> None:
        """
        Send the close that was waiting for the protective order to go (#503).

        Args:
            position_id: The position whose protective order is now cancelled
        """
        deferred = self._deferred_closes.pop(position_id, None)
        if deferred is None:
            return
        if deferred.reinstate:
            self._reinstate_after_close.add(position_id)
        self.logger.info(
            f'🛡️ The venue confirmed the protective order for {position_id} is gone — '
            f'sending the close now')
        self.close_position(
            position_id, lots=deferred.lots, close_reason=deferred.close_reason)

    def _abandon_deferred_close(self, position_id: str, why: str) -> None:
        """
        Drop a close that can no longer be sent safely (#503).

        The position stays OPEN and, because the cancel did not go through, still
        protected — the safe end of the two outcomes, and the operator has to know the
        close they asked for did not happen.

        Args:
            position_id: The position whose close was waiting
            why: What stopped it
        """
        if self._deferred_closes.pop(position_id, None) is None:
            return
        self.logger.error(
            f'❌ The close of {position_id} was withheld and is now abandoned: {why}. '
            f'The position is STILL OPEN and its protective order is still at the venue.')

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
            existing = self.portfolio.get_position(position_id)
            previous_stop = existing.stop_loss if existing else None
            result = self.portfolio.modify_position(
                position_id=position_id,
                new_stop_loss=new_stop_loss,
                new_take_profit=new_take_profit,
            )
            if result.success:
                # #503 — the level moved locally; the order that HOLDS it has to follow,
                # or the venue keeps enforcing the old one and the console shows the new.
                self._follow_protective_level(position_id, previous_stop)
            return result

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
        # Adapter must implement build_position_modify_payload /
        # do_request_position_modify / parse_position_modify_response
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

    def _follow_protective_level(
        self,
        position_id: str,
        previous_stop: Optional[float],
    ) -> None:
        """
        Move the venue's protective order to the position's new stop level (#503).

        AMEND rather than cancel-and-replace: an amend keeps the order's place and its
        identity, and — this is the operational half — it never leaves a window in which
        the position has no protection at the venue at all. A cancel-replace does, and a
        trailing stop would open that window on nearly every tick of a trend.

        A level REMOVED (`stop_loss` set to None) is the other direction and cannot be
        amended into: the order itself has to go, or the venue keeps enforcing a level the
        strategy has withdrawn.

        Args:
            position_id: The position whose level moved
            previous_stop: What its stop was before the change
        """
        position = self.portfolio.get_position(position_id)
        if position is None or not position.protective_broker_ref:
            return
        if position.stop_loss == previous_stop:
            return
        order_id = position.protective_order_id
        if order_id is None:
            return

        if position.stop_loss is None:
            self._cancel_resting_order(
                order_id, self._active_stop_orders, 'protective stop')
            self.logger.info(
                f'🛡️ The stop for {position_id} was withdrawn — cancelling the order the '
                f'venue was holding for it')
            return

        result = self.modify_stop_order(order_id, new_stop_price=position.stop_loss)
        if result.success:
            self.logger.info(
                f'🛡️ Protective STOP for {position_id} amended to '
                f'{position.stop_loss:.5f} (was {previous_stop})')
            return
        # The books now say one level and the venue holds another. Which one is real is
        # the venue's, so the operator has to hear about it (§35).
        self.logger.error(
            f'❌ The protective order for {position_id} could NOT follow the new stop '
            f'{position.stop_loss:.5f}: {result.rejection_reason}. The venue is still '
            f'holding {previous_stop} — the level shown and the level enforced differ.')

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
        # #503 — a protective order is EXEMPT from the cancel loop, whatever the policy
        # says. It is the one order whose entire purpose is to outlive this process, and
        # the only pair a session can start with today (orders=cancel + positions=leave)
        # would otherwise cancel the protection at exactly the moment the bot stops
        # looking — the precise inversion of what it is for.
        # The exemption covers a protective order that still HAS something to protect. An
        # ORPHAN — one whose position is gone — is the opposite case and must be cancelled
        # like anything else: a stop resting over a holding that no longer exists sells
        # coins that are not ours to sell, and in a shared account (#489) they belong to
        # someone else. Kraken links nothing, so this is our cleanup or nobody's.
        protective = [p for p in resting
                      if p.closes_position_id
                      and self.portfolio.get_position(p.closes_position_id) is not None]
        orphans = [p for p in resting
                   if p.closes_position_id and p not in protective]
        if protective:
            self.logger.info(
                f'🛡️ {len(protective)} protective order(s) LEFT STANDING at the venue — '
                f'they are what protects the open positions while nothing is running, so '
                f'the session-end cancel policy does not apply to them.')
        if orphans:
            self.logger.error(
                f'❌ {len(orphans)} protective order(s) are resting at the venue over a '
                f'position this bot no longer holds — cancelling them with the rest. A '
                f'stop over a holding that is gone sells coins that are not ours.')
        resting = [p for p in resting if p not in protective]

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
