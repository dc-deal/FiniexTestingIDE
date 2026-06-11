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
"""

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.config_types.autotrader_defaults_config_types import ReconciliationDefaults
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.live_types.reconciliation_types import (
    BrokerOrder,
    BrokerPosition,
    FlatCheckResult,
    ReconciliationResult,
)
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder

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
        self._config = config
        self._logger = logger
        self._trading_model = trading_model
        self._symbol = symbol

        self._last_reconcile_tick: int = 0
        self._last_reconcile_time: float = time.monotonic()
        self._reconcile_count: int = 0
        self._divergence_count: int = 0          # cumulative (session total, for the summary)
        self._last_divergence_count: int = 0     # current cycle (for the SESSION panel)
        self._last_clean: bool = True
        self._state_since: float = time.monotonic()  # when the current clean/divergent state began

        self._logger.info(
            f"🔍 Reconciler active (ALERT_ONLY) — cadence: every {config.interval_ticks} ticks "
            f"or {config.min_interval_seconds}s | model={trading_model.value}"
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

        Args:
            current_tick: Current tick-loop counter (updates the cadence tracker)

        Returns:
            ReconciliationResult for this cycle
        """
        broker_orders = self._adapter.get_broker_orders()
        local_orders = self._executor.get_active_orders()

        if self._trading_model == TradingModel.MARGIN:
            broker_positions = self._adapter.get_broker_positions()
            local_positions = self._portfolio.get_open_positions()
        else:
            broker_positions, local_positions = [], []

        result = self._diff(broker_positions, broker_orders, local_positions, local_orders)
        self._handle_result(result)

        # Per-cycle heartbeat — divergences are WARNed in _handle_result; a clean
        # cycle logs a concise INFO line so the poll is visible in the session log.
        if result.is_clean:
            skipped = sum(1 for o in local_orders if not self._is_reconcilable_ref(o.broker_ref))
            skipped_note = f" ({skipped} dry-run/in-flight skipped)" if skipped else ""
            self._logger.info(
                f"🔍 reconcile #{self._reconcile_count}: clean — "
                f"broker_orders={len(broker_orders)} local_orders={len(local_orders)}{skipped_note}"
            )

        self._last_reconcile_tick = current_tick
        self._last_reconcile_time = time.monotonic()
        return result

    def _diff(
        self,
        broker_positions: List[BrokerPosition],
        broker_orders: List[BrokerOrder],
        local_positions: List[Position],
        local_orders: List[PendingOrder],
    ) -> ReconciliationResult:
        """
        Compute the divergence buckets. Match by broker_ref.

        In-flight (broker_ref=None) and dry-run (DRYRUN-*) local orders are
        excluded — they are mid-roundtrip / synthetic and would otherwise read
        as false orphans (mirrors the DriftAuditor's DRYRUN skip).

        Args:
            broker_positions: Broker truth positions (MARGIN; [] on SPOT)
            broker_orders: Broker truth resting orders
            local_positions: Local shadow positions (MARGIN; [] on SPOT)
            local_orders: Local resting orders (executor.get_active_orders())

        Returns:
            ReconciliationResult with all buckets + is_clean
        """
        # --- Orders (world-agnostic) ---
        local_orders_by_ref: Dict[str, PendingOrder] = {
            o.broker_ref: o
            for o in local_orders
            if self._is_reconcilable_ref(o.broker_ref)
        }
        broker_orders_by_ref: Dict[str, BrokerOrder] = {
            o.broker_ref: o for o in broker_orders if o.broker_ref
        }

        ghost_orders = [
            bo for ref, bo in broker_orders_by_ref.items()
            if ref not in local_orders_by_ref
        ]
        orphan_orders = [
            lo for ref, lo in local_orders_by_ref.items()
            if ref not in broker_orders_by_ref
        ]
        stale_orders: List[Tuple[PendingOrder, BrokerOrder]] = [
            (lo, broker_orders_by_ref[ref])
            for ref, lo in local_orders_by_ref.items()
            if ref in broker_orders_by_ref and self._order_is_stale(lo, broker_orders_by_ref[ref])
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
        is_clean = not (
            ghost_positions or orphan_positions or stale_positions
            or ghost_orders or orphan_orders or stale_orders
        )

        return ReconciliationResult(
            timestamp=datetime.now(timezone.utc),
            ghost_positions=ghost_positions,
            orphan_positions=orphan_positions,
            stale_positions=stale_positions,
            ghost_orders=ghost_orders,
            orphan_orders=orphan_orders,
            stale_orders=stale_orders,
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

        # Current-cycle divergence count (snapshot) — drives the SESSION panel, so
        # it resets to 0 when a divergence is resolved (panel returns to ● ok).
        n = (
            len(result.ghost_positions) + len(result.orphan_positions) + len(result.stale_positions)
            + len(result.ghost_orders) + len(result.orphan_orders) + len(result.stale_orders)
        )
        self._last_divergence_count = n
        if result.is_clean:
            return

        self._divergence_count += n   # cumulative session total (final summary)
        self._logger.warning(
            f"[RECONCILE] {n} divergence(s) detected (ALERT_ONLY)\n"
            f"   orders     ghost={len(result.ghost_orders)} "
            f"orphan={len(result.orphan_orders)} stale={len(result.stale_orders)}\n"
            f"   positions  ghost={len(result.ghost_positions)} "
            f"orphan={len(result.orphan_positions)} stale={len(result.stale_positions)}\n"
            f"   partial_fills={len(result.partial_fills)} (observed, not a divergence)"
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
            / clean / count / state_age_s / next_in_s
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
        }

    def shutdown(self) -> None:
        """Emit a final one-line reconciliation summary to the session log."""
        self._logger.info(
            f"🔍 Reconciliation final: {self._reconcile_count} cycles | "
            f"{self._divergence_count} total divergence(s) (ALERT_ONLY)"
        )
