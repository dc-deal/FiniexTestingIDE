"""
FiniexTestingIDE - Reconciler (#151, Phase 2)

Detects divergence between the local shadow state and broker truth, ALERT_ONLY:
it logs and counts divergences but does not mutate state and does not halt.
Correction (AUTO_CORRECT) and HALT_TRADING land in #349 (V1.4).

Live-only by design (Design Decision #9 — sim's PortfolioManager IS the truth).
Poll-based: the tick loop calls is_due() on a hybrid cadence (every N ticks OR
every M wall-clock seconds) and then reconcile(). The outcome-listener path is
Phase 4 (#349), not here.

TradingModel gates the position diff:
    SPOT   → reconcile resting ORDERS only (broker has no position object;
             holdings are balances). Flat-preflight uses balances + orders.
    MARGIN → additionally reconcile POSITIONS (lights up with the MT5 adapter, #209).

Divergence vocabulary:
    ghost  — broker has it, we lack it locally
    orphan — we have it locally, broker lacks it
    stale  — matched by broker_ref but a field diverges

Since #355 the order diff joins on the CLIENT order id first, so a resting order we
placed is no longer an anonymous ghost:
    attributed      — our key, and a local pending is still waiting for its reference
    abandoned       — our key, this session, nothing local left
    foreign_session — our key shape, another session (boot adoption is #355 Phase 2)
    unconfirmed     — local pending, submit never answered, not at the broker either
The write that completes an attribution belongs to the executor; this class only ever
reads (ALERT_ONLY).
"""

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.config_types.autotrader_defaults_config_types import (
    ReconciliationDefaults,
)
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.connection_types import ConnectionOutcome
from python.framework.types.live_types.reconciliation_types import (
    BrokerOrder,
    BrokerPosition,
    FlatCheckResult,
    ReconciliationResult,
)
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOperation,
    PendingOrder,
)
from python.framework.utils.run_id_utils import parse_client_order_id

if TYPE_CHECKING:
    from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor


# Relative tolerance (percent) for the stale-field comparison.
_STALE_TOLERANCE_PCT = 0.1
# Asset balance magnitude below which a balance counts as dust (flat-preflight).
_DUST_THRESHOLD = 1e-8


class Reconciler:
    """
    Read-only broker-vs-local reconciliation for live trading (ALERT_ONLY).

    Pulls broker truth via the adapter's get_broker_* methods and diffs it
    against the executor's local shadow state (resting orders, positions). On
    SPOT only orders are diffed; positions are MARGIN-only. Detected divergences
    are logged with a [RECONCILE] prefix and counted for the SESSION panel.

    Args:
        executor: LiveTradeExecutor — provides adapter, portfolio, processor,
            and the resting-order accessor.
        config: ReconciliationDefaults — cadence + mode (only alert_only here).
        logger: AbstractLogger — session logger.
        trading_model: SPOT or MARGIN — gates the position diff.
        symbol: Traded symbol — resolves the quote currency for the flat-check.
    """

    def __init__(
        self,
        executor: 'LiveTradeExecutor',
        config: ReconciliationDefaults,
        logger: AbstractLogger,
        trading_model: TradingModel,
        symbol: str,
    ):
        if config.mode != 'alert_only':
            raise NotImplementedError(
                f"reconciliation mode '{config.mode}' is not implemented in #151 — "
                f"AUTO_CORRECT / HALT_TRADING land in #349. Use 'alert_only'."
            )

        self._executor = executor
        self._adapter = executor.broker.adapter
        self._portfolio = executor.portfolio
        self._rest_ladder = executor.get_rest_ladder()
        self._config = config
        self._logger = logger
        self._trading_model = trading_model
        self._symbol = symbol

        self._skipped_count: int = 0             # cycles that could not reach the broker (#473)
        self._last_skipped_reason: Optional[str] = None
        self._last_reconcile_tick: int = 0
        self._last_reconcile_time: float = time.monotonic()
        self._reconcile_count: int = 0
        self._divergence_count: int = 0          # cumulative (session total, for the summary)
        self._last_divergence_count: int = 0     # current cycle (for the SESSION panel)
        self._attributed_count: int = 0          # orders reclaimed by client order id (#355)
        self._last_unaccounted_count: int = 0    # current cycle: ours, but not accounted for
        # Orders already reported as unconfirmed — the pot gets each one once, not once
        # per cycle (#355). An order that is later ATTRIBUTED is removed again: it is no
        # longer unaccounted for, and the final summary counts this set.
        self._reported_unconfirmed: Set[str] = set()
        # Broker references already named as ours-but-unaccounted, and the fingerprint of the
        # last divergence picture. Both exist for the same reason: most of these states are
        # DURABLE — a resting order stays resting — so repeating them per cycle would bury the
        # report rather than inform it.
        self._reported_ours: Set[str] = set()
        self._last_divergence_fingerprint: Optional[str] = None
        self._unchanged_cycles: int = 0
        self._last_clean: bool = True
        self._state_since: float = time.monotonic()  # when the current clean/divergent state began

        self._logger.info(
            f'🔍 Reconciler active (ALERT_ONLY) — cadence: every {config.interval_ticks} ticks '
            f'or {config.min_interval_seconds}s | model={trading_model.value}'
        )

    # ============================================
    # Cadence
    # ============================================

    def is_due(self, tick_counter: int) -> bool:
        """
        Whether a reconcile is due — hybrid cadence (ticks OR wall-clock).

        Args:
            tick_counter: Current tick-loop counter

        Returns:
            True if interval_ticks elapsed OR min_interval_seconds elapsed
        """
        if tick_counter - self._last_reconcile_tick >= self._config.interval_ticks:
            return True
        return (time.monotonic() - self._last_reconcile_time) >= self._config.min_interval_seconds

    # ============================================
    # Reconcile
    # ============================================

    def reconcile(self, current_tick: int = 0) -> ReconciliationResult:
        """
        Pull broker truth, diff against local shadow state, handle (ALERT_ONLY).

        The broker pull is the one step that reaches outside the process, and a venue
        answering 502 for a moment must not end a thirty-day session (#473). On a TRANSIENT
        failure the cycle is SKIPPED and reported: this method is already cadenced, so
        `is_due` is the ladder and no wait belongs in the tick loop. A TERMINAL failure
        still propagates — a refused credential is not something to keep quiet about.

        Args:
            current_tick: Current tick-loop counter (updates the cadence tracker)

        Returns:
            ReconciliationResult for this cycle, or a skipped one when broker truth was
            unreachable
        """
        try:
            broker_orders = self._adapter.get_broker_orders()
            local_orders = self._executor.get_active_orders()

            if self._trading_model == TradingModel.MARGIN:
                broker_positions = self._adapter.get_broker_positions()
                local_positions = self._portfolio.get_open_positions()
            else:
                broker_positions, local_positions = [], []
        except Exception as error:   # noqa: BLE001 — classified, never swallowed
            if self._rest_ladder.classify(error) is not ConnectionOutcome.TRANSIENT:
                raise
            return self._skip_cycle(current_tick, error)

        result = self._diff(broker_positions, broker_orders, local_positions, local_orders)
        self._handle_result(result)

        # Per-cycle heartbeat — divergences are WARNed in _handle_result; a clean
        # cycle logs a concise INFO line so the poll is visible in the session log.
        if result.is_clean:
            skipped = sum(1 for o in local_orders if not self._is_reconcilable_ref(o.broker_ref))
            skipped_note = f' ({skipped} dry-run/in-flight skipped)' if skipped else ''
            self._logger.info(
                f'🔍 reconcile #{self._reconcile_count}: clean — '
                f'broker_orders={len(broker_orders)} local_orders={len(local_orders)}{skipped_note}'
            )

        self._last_reconcile_tick = current_tick
        self._last_reconcile_time = time.monotonic()
        return result

    def _skip_cycle(self, current_tick: int, error: BaseException) -> ReconciliationResult:
        """
        Record a cycle that could not compare anything, and advance the cadence.

        Advancing the cadence is deliberate: without it an unreachable venue would make
        every heartbeat re-attempt the pull at full speed, which is a retry storm wearing
        a cadence for a hat. The next attempt comes at the normal interval.

        Args:
            current_tick: Current tick-loop counter
            error: The transport failure that ended the pull

        Returns:
            A ReconciliationResult carrying only skipped_reason
        """
        self._reconcile_count += 1
        self._skipped_count += 1
        self._last_skipped_reason = str(error)
        self._last_reconcile_tick = current_tick
        self._last_reconcile_time = time.monotonic()

        self._logger.warning(
            f'🔍 reconcile #{self._reconcile_count}: SKIPPED — broker truth unreachable '
            f'({error}) · next attempt in {self._config.min_interval_seconds:.0f}s'
        )
        return ReconciliationResult(
            timestamp=datetime.now(timezone.utc),
            is_clean=False,
            skipped_reason=str(error),
        )

    def _diff(
        self,
        broker_positions: List[BrokerPosition],
        broker_orders: List[BrokerOrder],
        local_positions: List[Position],
        local_orders: List[PendingOrder],
    ) -> ReconciliationResult:
        """
        Compute the divergence buckets. Match by client order id, then by broker_ref.

        The client order id joins FIRST because it is OURS (#355): it still answers
        "whose order is this" when the venue's own reference never reached us, which is
        exactly the state a submit leaves behind when its answer is lost (#473).
        broker_ref remains the join for everything already confirmed.

        Dry-run (DRYRUN-*) local orders stay excluded, and a local order still inside its
        normal submit roundtrip (broker_ref=None, no in-flight submit marker) is excluded
        too — both would otherwise read as false orphans (mirrors the DriftAuditor's
        DRYRUN skip).

        Args:
            broker_positions: Broker truth positions (MARGIN; [] on SPOT)
            broker_orders: Broker truth resting orders
            local_positions: Local shadow positions (MARGIN; [] on SPOT)
            local_orders: Local resting orders (executor.get_active_orders())

        Returns:
            ReconciliationResult with all buckets + is_clean
        """
        # --- Orders (world-agnostic) ---
        session_key = self._executor.get_session_key()

        local_orders_by_ref: Dict[str, PendingOrder] = {
            o.broker_ref: o
            for o in local_orders
            if self._is_reconcilable_ref(o.broker_ref)
        }
        # Only pendings waiting for a LOST answer are indexed by client key. Two exclusions,
        # both load-bearing: one already matched by ref must not fill a second bucket, and a
        # pending inside its ORDINARY submit roundtrip has lost nothing — attributing it
        # would report a repair that never happened. PENDING_SUBMIT is the precise marker;
        # it is written only where an unresolved submit is kept (#473). This also keeps
        # DRYRUN pendings out, whose synthetic ref is not None.
        local_orders_by_ckey: Dict[str, PendingOrder] = {}
        for o in local_orders:
            if o.broker_ref is not None:
                continue
            if o.execution_state.in_flight_operation is not PendingOperation.PENDING_SUBMIT:
                continue
            ckey = self._executor.build_client_order_id(o.pending_order_id)
            if ckey:
                local_orders_by_ckey[ckey] = o

        broker_orders_by_ref: Dict[str, BrokerOrder] = {
            o.broker_ref: o for o in broker_orders if o.broker_ref
        }
        broker_orders_by_ckey: Dict[str, BrokerOrder] = {
            o.client_order_id: o for o in broker_orders if o.client_order_id and o.broker_ref
        }
        # Orders the latency queue is still waiting on. They are OURS and they are TRACKED —
        # just not in the resting-order list the diff compares against, so without this they
        # would be reported as abandoned on the one cycle that catches them in flight.
        in_flight_ckeys = {
            self._executor.build_client_order_id(order_id)
            for order_id in self._executor.get_in_flight_order_ids()
        }

        attributed_orders: List[Tuple[PendingOrder, BrokerOrder]] = [
            (lo, broker_orders_by_ckey[ckey])
            for ckey, lo in local_orders_by_ckey.items()
            if ckey in broker_orders_by_ckey
        ]
        attributed_refs = {bo.broker_ref for _, bo in attributed_orders}

        # A broker order neither join found is UNCLAIMED, and what that means is decided
        # by the key it carries rather than by its absence from our books.
        ghost_orders: List[BrokerOrder] = []
        abandoned_orders: List[BrokerOrder] = []
        foreign_session_orders: List[BrokerOrder] = []
        for ref, bo in broker_orders_by_ref.items():
            if ref in local_orders_by_ref or ref in attributed_refs:
                continue
            if bo.client_order_id in in_flight_ckeys:
                continue
            parsed = parse_client_order_id(bo.client_order_id)
            if parsed is None:
                ghost_orders.append(bo)
            elif parsed[0] == session_key:
                abandoned_orders.append(bo)
            else:
                foreign_session_orders.append(bo)

        orphan_orders = [
            lo for ref, lo in local_orders_by_ref.items()
            if ref not in broker_orders_by_ref
        ]
        stale_orders: List[Tuple[PendingOrder, BrokerOrder]] = [
            (lo, broker_orders_by_ref[ref])
            for ref, lo in local_orders_by_ref.items()
            if ref in broker_orders_by_ref and self._order_is_stale(lo, broker_orders_by_ref[ref])
        ]

        # A pending whose submit was never answered AND which the venue does not show:
        # it blocks has_pending_orders() and nothing times it out, because the resting-order
        # list has no timeout at all. PENDING_SUBMIT is the precise marker — it is written
        # only where an unresolved submit is kept (#473), never on the normal roundtrip.
        attributed_ids = {lo.pending_order_id for lo, _ in attributed_orders}
        unconfirmed_orders = [
            lo for lo in local_orders
            if not lo.broker_ref
            and lo.execution_state.in_flight_operation is PendingOperation.PENDING_SUBMIT
            and lo.pending_order_id not in attributed_ids
        ]

        # --- Positions (MARGIN only; both lists empty on SPOT) ---
        local_pos_by_ref: Dict[str, Position] = {
            p.broker_ref: p for p in local_positions if p.broker_ref
        }
        broker_pos_by_ref: Dict[str, BrokerPosition] = {
            p.broker_ref: p for p in broker_positions if p.broker_ref
        }
        ghost_positions = [
            bp for ref, bp in broker_pos_by_ref.items()
            if ref not in local_pos_by_ref
        ]
        orphan_positions = [
            lp for ref, lp in local_pos_by_ref.items()
            if ref not in broker_pos_by_ref
        ]
        stale_positions: List[Tuple[Position, BrokerPosition]] = [
            (lp, broker_pos_by_ref[ref])
            for ref, lp in local_pos_by_ref.items()
            if ref in broker_pos_by_ref and self._position_is_stale(lp, broker_pos_by_ref[ref])
        ]

        # --- Partial fills (observation only; deterministic detection → #342) ---
        partial_fills = [
            lo for lo in local_orders
            if lo.lots and 0.0 < lo.fills.cumulative_filled_lots < lo.lots
        ]

        # partial_fills do NOT affect is_clean — a partial fill is a normal
        # market outcome (observed, not a divergence; #349 delta-applies it).
        # attributed_orders do not either: a reclaimed reference is a repair, and a cycle
        # that repairs something is not the same thing as a cycle that found damage.
        is_clean = not (
            ghost_positions or orphan_positions or stale_positions
            or ghost_orders or orphan_orders or stale_orders
            or abandoned_orders or foreign_session_orders or unconfirmed_orders
        )

        return ReconciliationResult(
            timestamp=datetime.now(timezone.utc),
            ghost_positions=ghost_positions,
            orphan_positions=orphan_positions,
            stale_positions=stale_positions,
            ghost_orders=ghost_orders,
            orphan_orders=orphan_orders,
            stale_orders=stale_orders,
            attributed_orders=attributed_orders,
            abandoned_orders=abandoned_orders,
            foreign_session_orders=foreign_session_orders,
            unconfirmed_orders=unconfirmed_orders,
            partial_fills=partial_fills,
            is_clean=is_clean,
        )

    def _order_is_stale(self, local: PendingOrder, broker: BrokerOrder) -> bool:
        """
        Whether a broker_ref-matched order pair diverges on price or lots.

        Args:
            local: Local resting PendingOrder
            broker: Broker-reported BrokerOrder

        Returns:
            True if the limit price or lots differ beyond tolerance
        """
        local_price = (local.order_kwargs or {}).get('limit_price')
        if local_price is not None and broker.price is not None and not self._within_tol(local_price, broker.price):
            return True
        if local.lots is not None and not self._within_tol(local.lots, broker.lots):
            return True
        return False

    def _position_is_stale(self, local: Position, broker: BrokerPosition) -> bool:
        """
        Whether a broker_ref-matched position pair diverges on price or lots.

        Args:
            local: Local shadow Position
            broker: Broker-reported BrokerPosition

        Returns:
            True if the entry price or lots differ beyond tolerance
        """
        if not self._within_tol(local.entry_price, broker.entry_price):
            return True
        return not self._within_tol(local.lots, broker.lots)

    @staticmethod
    def _within_tol(a: float, b: float) -> bool:
        """
        Whether two values agree within the relative stale tolerance.

        Args:
            a: Local value
            b: Broker value (reference for the relative delta)

        Returns:
            True if abs(a - b) / |b| is within _STALE_TOLERANCE_PCT
        """
        denom = max(abs(b), 1e-12)
        return abs(a - b) / denom * 100.0 <= _STALE_TOLERANCE_PCT

    @staticmethod
    def _is_reconcilable_ref(broker_ref: Optional[str]) -> bool:
        """
        Whether a local order's broker_ref is eligible for the diff.

        Excludes in-flight orders (broker_ref=None, mid submit-roundtrip) and
        dry-run synthetic orders (DRYRUN-*) — both would otherwise read as false
        orphans against the broker truth.

        Args:
            broker_ref: The local order's broker reference

        Returns:
            True if the ref is a settled real broker reference
        """
        return bool(broker_ref) and not broker_ref.startswith('DRYRUN-')

    # ============================================
    # ALERT_ONLY handling
    # ============================================

    def _handle_result(self, result: ReconciliationResult) -> None:
        """
        Log + count divergences. ALERT_ONLY — no mutation, no halt.

        Args:
            result: The diff outcome for this cycle
        """
        self._reconcile_count += 1
        # State-transition timer: reset when clean↔divergent flips, so the panel
        # can show "clean for Xs" (stability) / "divergent for Xs" (persistence).
        if result.is_clean != self._last_clean:
            self._state_since = time.monotonic()
        self._last_clean = result.is_clean

        # An attribution is reported whether or not the cycle is otherwise clean — it is
        # the one outcome here that CHANGED something, so it must not hide behind is_clean.
        self._report_attributions(result)
        self._report_unconfirmed(result)

        # The panel's own headline for "ours, and we cannot account for it" — a snapshot
        # like the divergence count below, so it clears when the state clears.
        self._last_unaccounted_count = (
            len(result.abandoned_orders) + len(result.foreign_session_orders)
            + len(result.unconfirmed_orders)
        )

        # Current-cycle divergence count (snapshot) — drives the SESSION panel, so
        # it resets to 0 when a divergence is resolved (panel returns to ● ok).
        n = (
            len(result.ghost_positions) + len(result.orphan_positions) + len(result.stale_positions)
            + len(result.ghost_orders) + len(result.orphan_orders) + len(result.stale_orders)
            + len(result.abandoned_orders) + len(result.foreign_session_orders)
            + len(result.unconfirmed_orders)
        )
        self._last_divergence_count = n
        if result.is_clean:
            return

        self._divergence_count += n   # cumulative session total (final summary)

        # A divergence picture that has not CHANGED has nothing new to say, and most of these
        # states are durable: a resting order stays resting until somebody cancels it, unlike
        # a ghost that resolves on the next fill. Repeating the full warning every cycle would
        # put up to ~43,000 identical lines into the session pot over a thirty-day run at the
        # 60 s floor — which does not inform, it buries. So the warning fires on CHANGE, and an
        # unchanged cycle says so in one INFO line, which keeps the poll visibly alive.
        fingerprint = self._divergence_fingerprint(result)
        if fingerprint == self._last_divergence_fingerprint:
            self._unchanged_cycles += 1
            self._logger.info(
                f'🔍 reconcile #{self._reconcile_count}: {n} divergence(s) UNCHANGED '
                f'(same for {self._unchanged_cycles} cycle(s)) — see the first report'
            )
            return

        self._last_divergence_fingerprint = fingerprint
        self._unchanged_cycles = 0
        self._logger.warning(
            f'[RECONCILE] {n} divergence(s) detected (ALERT_ONLY)\n'
            f'   orders     ghost={len(result.ghost_orders)} '
            f'orphan={len(result.orphan_orders)} stale={len(result.stale_orders)}\n'
            f'   ours       abandoned={len(result.abandoned_orders)} '
            f'foreign_session={len(result.foreign_session_orders)} '
            f'unconfirmed={len(result.unconfirmed_orders)}\n'
            f'   positions  ghost={len(result.ghost_positions)} '
            f'orphan={len(result.orphan_positions)} stale={len(result.stale_positions)}\n'
            f'   partial_fills={len(result.partial_fills)} (observed, not a divergence)'
        )
        self._report_our_unaccounted(result)

    @staticmethod
    def _divergence_fingerprint(result: ReconciliationResult) -> str:
        """
        What the divergence picture consists of, as one comparable string.

        Identities rather than counts: one ghost replaced by a different ghost is a CHANGE the
        operator needs, even though the count stayed at one.

        Args:
            result: The diff outcome for this cycle

        Returns:
            A stable fingerprint of every divergence bucket's members
        """
        parts = [
            'g:' + ','.join(sorted(o.broker_ref for o in result.ghost_orders)),
            'a:' + ','.join(sorted(o.broker_ref for o in result.abandoned_orders)),
            'f:' + ','.join(sorted(o.broker_ref for o in result.foreign_session_orders)),
            'u:' + ','.join(sorted(p.pending_order_id for p in result.unconfirmed_orders)),
            'o:' + ','.join(sorted(p.pending_order_id for p in result.orphan_orders)),
            's:' + ','.join(sorted(lo.pending_order_id for lo, _ in result.stale_orders)),
            'gp:' + str(len(result.ghost_positions)),
            'op:' + str(len(result.orphan_positions)),
            'sp:' + str(len(result.stale_positions)),
        ]
        return '|'.join(parts)

    def _report_our_unaccounted(self, result: ReconciliationResult) -> None:
        """
        Name each order that is OURS by key and that we cannot account for — once.

        The bucket counts above say how many; this says which, and an operator cannot act on a
        number. Once per broker reference, because the state is durable: the order is still
        there next cycle and saying so again adds nothing.

        Args:
            result: The diff outcome for this cycle
        """
        for order in result.abandoned_orders:
            if order.broker_ref in self._reported_ours:
                continue
            self._reported_ours.add(order.broker_ref)
            self._logger.warning(
                f'🔍 order {order.broker_ref} carries THIS session\'s key '
                f'({order.client_order_id}) but we no longer track it — placed and forgotten. '
                f'It is still resting at the broker.'
            )

        for order in result.foreign_session_orders:
            if order.broker_ref in self._reported_ours:
                continue
            self._reported_ours.add(order.broker_ref)
            self._logger.warning(
                f'🔍 order {order.broker_ref} carries a key of our shape from ANOTHER session '
                f'({order.client_order_id}) — an earlier run of this bot, or a lost carry-over. '
                f'Boot adoption would have claimed it (#355); mid-session it is left alone.'
            )

    def _report_attributions(self, result: ReconciliationResult) -> None:
        """
        Name every order the client key reclaimed from broker truth (#355).

        The executor performs the write (the Reconciler stays read-only); this is the
        record that it happened, and it names both keys so the operator can follow the
        order from our books to the venue's.

        Args:
            result: The diff outcome for this cycle
        """
        for pending, broker_order in result.attributed_orders:
            self._attributed_count += 1
            # It was reported as unaccounted for on an earlier cycle; it is not any more,
            # and the final summary must not keep claiming it.
            self._reported_unconfirmed.discard(pending.pending_order_id)
            self._logger.info(
                f'🔍 reconcile #{self._reconcile_count}: attributed — order '
                f'{pending.pending_order_id} (client_order_id={broker_order.client_order_id}) '
                f'is resting at the broker as {broker_order.broker_ref}. Its submit answer '
                f'was lost; the reference is restored and polling resumes.'
            )

    def _report_unconfirmed(self, result: ReconciliationResult) -> None:
        """
        Report a pending that was never confirmed and is not at the broker either.

        Edge-triggered per order: a cycle-by-cycle repeat would fill the session error pot
        with one fact. It is an ERROR rather than a warning on purpose (§35) — such an
        order keeps has_pending_orders() true, so an algo that waits on it stops trading
        for the rest of the session, and a session in that state must not grade green.

        Args:
            result: The diff outcome for this cycle
        """
        for pending in result.unconfirmed_orders:
            if pending.pending_order_id in self._reported_unconfirmed:
                continue
            self._reported_unconfirmed.add(pending.pending_order_id)
            self._logger.error(
                f'❌ Order {pending.pending_order_id} was submitted but never confirmed, '
                f'and the broker does not show it either. It may still have been accepted, '
                f'so it is NOT dropped — but it keeps has_pending_orders() true, which '
                f'blocks any algo waiting on it. Resolving it needs a targeted order-status '
                f'query (#487); until then, check the account by hand.'
            )

    # ============================================
    # Flat-preflight (consumed by the Field Study #332)
    # ============================================

    def is_account_flat(self) -> FlatCheckResult:
        """
        One-time flat check against broker truth.

        Spot: flat means no resting broker orders AND no non-quote asset balance
        above the dust threshold. The quote currency is resolved from the traded
        symbol's specification.

        Returns:
            FlatCheckResult (is_flat + blocking orders/balances + reasons)
        """
        broker_orders = self._adapter.get_broker_orders()
        balances = self._adapter.get_broker_balances()
        quote_currency = self._adapter.get_symbol_specification(self._symbol).quote_currency

        asset_balances = {
            asset: amount
            for asset, amount in balances.items()
            if self._normalize_asset(asset) != quote_currency and abs(amount) > _DUST_THRESHOLD
        }

        reasons: List[str] = []
        if broker_orders:
            reasons.append(f'{len(broker_orders)} resting broker order(s)')
        if asset_balances:
            reasons.append(f'non-quote asset balances: {asset_balances}')

        return FlatCheckResult(
            is_flat=not broker_orders and not asset_balances,
            open_orders=broker_orders,
            asset_balances=asset_balances,
            reasons=reasons,
        )

    @staticmethod
    def _normalize_asset(code: str) -> str:
        """
        Normalize a broker asset code to a standard currency code.

        Handles Kraken's legacy prefixes (X for crypto, Z for fiat on 4-char
        codes) and the XBT→BTC alias. Best-effort; validated against the real
        API by the Field Study (#332) / live-adapter tests.

        Args:
            code: Broker asset code (e.g. 'ZUSD', 'XETH', 'XXBT')

        Returns:
            Standard currency code (e.g. 'USD', 'ETH', 'BTC')
        """
        if len(code) == 4 and code[0] in ('X', 'Z'):
            code = code[1:]
        return 'BTC' if code == 'XBT' else code

    # ============================================
    # Accessors + shutdown
    # ============================================

    def get_display_counters(self) -> Dict[str, object]:
        """
        Slim counter dict for the SESSION-panel reconcile status line.

        divergences is the CURRENT cycle's count (snapshot — resets to 0 when a
        divergence resolves, so the panel returns to ● ok); total_divergences is
        the cumulative session total (for the final summary). state_age_s = seconds
        in the current clean/divergent state (panel: "clean for Xs" / "for Xs").
        next_in_s is the time-based bound to the next reconcile (may fire sooner on
        the interval_ticks threshold — "≤"). count = cycles run (0 = no check yet).

        Returns:
            reconcile_enabled / divergences (current) / total_divergences (cumulative)
            / clean / count / state_age_s / next_in_s / skipped (+reason) / attributed
            (session total) / unaccounted (current cycle)
        """
        now = time.monotonic()
        age = now - self._last_reconcile_time
        return {
            'reconcile_enabled': True,
            'reconcile_divergences': self._last_divergence_count,        # current cycle (panel headline)
            'reconcile_total_divergences': self._divergence_count,        # cumulative session total
            'reconcile_clean': self._last_clean,
            'reconcile_count': self._reconcile_count,
            'reconcile_state_age_s': now - self._state_since,             # time in current clean/divergent state
            'reconcile_next_in_s': max(0.0, self._config.min_interval_seconds - age),
            # #473: a skipped cycle verified nothing. Without this the panel would show a
            # reconcile count climbing against an unreachable venue — "gave up" wearing the
            # face of "still checking".
            'reconcile_skipped': self._skipped_count,
            'reconcile_skipped_reason': self._last_skipped_reason,
            # #355: attributed is a SESSION TOTAL (a repair belongs in the log and the final
            # summary, not on the panel); unaccounted is a CURRENT-CYCLE snapshot like the
            # divergence count above, so it clears when the state clears.
            'reconcile_attributed': self._attributed_count,
            'reconcile_unaccounted': self._last_unaccounted_count,
        }

    def get_skipped_count(self) -> int:
        """
        Cycles that could not reach the broker at all (#473).

        Returns:
            Number of skipped cycles this session
        """
        return self._skipped_count

    def shutdown(self) -> None:
        """Emit a final one-line reconciliation summary to the session log."""
        skipped = (f' | {self._skipped_count} skipped (broker unreachable)'
                   if self._skipped_count else '')
        reclaimed = (f' | {self._attributed_count} order(s) reclaimed by client order id'
                     if self._attributed_count else '')
        unconfirmed = (f' | {len(self._reported_unconfirmed)} order(s) left unaccounted'
                       if self._reported_unconfirmed else '')
        self._logger.info(
            f'🔍 Reconciliation final: {self._reconcile_count} cycles | '
            f'{self._divergence_count} total divergence(s) (ALERT_ONLY)'
            f'{skipped}{reclaimed}{unconfirmed}'
        )
