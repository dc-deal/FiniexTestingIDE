"""
FiniexTestingIDE - Drift Auditor (#327)

Read-only telemetry: compares locally-computed fee/volume/price values
against broker-reported truth from #326 per-execution BrokerTrade records.

Triggers an async trades_query after every fully-filled order, consumes the
response via the executor's multi-consumer fan-out, and logs drift events
above thresholds. Does not mutate state — purely observational. Correction
is #151 Reconciliation Layer.

This is the first productive consumer of the async trades_query pipeline
established by #326. The pipeline has existed but has only been exercised
by tests until now.

Architecture:
    on_outcome(EXECUTED, pending)
        → snapshot synthetic state from pending.cumulative_*
        → store in self._pending_audits[order_id]
        → executor.submit_trades_query_async(order_id, broker_ref)

    on_trades_response(response)   (later, after broker roundtrip)
        → pop snapshot from self._pending_audits (always — no leak)
        → compute broker truth from response.trades (immutable)
        → compare local vs. broker, log drift events
"""

from datetime import datetime, timezone
from typing import Dict, Optional, TYPE_CHECKING

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.config_types.autotrader_defaults_config_types import DriftAuditConfig
from python.framework.types.live_types.drift_audit_types import (
    AuditContext,
    DriftAuditSummary,
    DriftRecord,
    DriftType,
)
from python.framework.types.live_types.live_request_types import TradesQueryResponse
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderResult,
    OrderStatus,
)

if TYPE_CHECKING:
    from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor


class DriftAuditor:
    """
    Read-only drift telemetry consumer for live trading sessions.

    Registers as a listener on the executor's outcome chain (#319) and a
    consumer on its trades-response fan-out (#327). Triggers post-fill
    trades_query to obtain broker truth, then compares against the
    synthetic state captured at outcome time.

    Live-only by design — simulation has no broker truth distinct from
    its local fee model. DRYRUN orders are skipped (broker_ref starts
    with 'DRYRUN-'). MOCK orders are audited (useful for tests).

    Args:
        executor: LiveTradeExecutor — provides listener/consumer registration
            and the submit_trades_query_async delegating method.
        config: DriftAuditConfig — thresholds, log_all, sample_rate.
        logger: AbstractLogger — session logger; drift events written with
            [DRIFT] prefix at WARNING level.
    """

    def __init__(
        self,
        executor: 'LiveTradeExecutor',
        config: DriftAuditConfig,
        logger: AbstractLogger,
    ):
        self._executor = executor
        self._config = config
        self._logger = logger
        self._pending_audits: Dict[str, AuditContext] = {}
        self._summary = DriftAuditSummary()

        executor.add_order_outcome_listener(self._on_order_outcome)
        executor.add_trades_response_consumer(self._on_trades_response)

        self._logger.info(
            f"📊 DriftAuditor active — thresholds: "
            f"fee={config.fee_threshold_pct}%, vol={config.volume_threshold_pct}%, "
            f"price={config.price_threshold_pct}% (structural — see #244)"
        )

    # ============================================
    # Listener — runs at outcome time, captures snapshot, triggers trades_query
    # ============================================

    def _on_order_outcome(
        self,
        direction: OrderDirection,
        result: OrderResult,
        pending: Optional[PendingOrder] = None,
    ) -> None:
        """
        Snapshot synthetic state at fill, schedule trades_query for real data.

        Skips non-EXECUTED outcomes, pre-submit rejections (pending=None),
        and DRYRUN orders (no real broker truth available).

        Args:
            direction: Order direction from listener signature
            result: Terminal OrderResult
            pending: PendingOrder reference (None for pre-submit rejections)
        """
        if not self._config.enabled:
            return
        if result.status != OrderStatus.EXECUTED:
            return
        if pending is None:
            return
        if pending.broker_ref is None or pending.broker_ref.startswith('DRYRUN-'):
            return

        snapshot = AuditContext(
            order_id=result.order_id,
            broker_ref=pending.broker_ref,
            symbol=pending.symbol,
            direction=direction,
            requested_lots=pending.lots,
            synthetic_cumulative_fee=pending.cumulative_fee,
            synthetic_cumulative_avg_price=pending.cumulative_avg_price,
            synthetic_cumulative_filled_lots=pending.cumulative_filled_lots,
            fee_currency=pending.trades[0].fee_currency if pending.trades else None,
        )
        self._pending_audits[result.order_id] = snapshot

        # Trigger real trades_query — broker round-trip happens on worker thread,
        # response surfaces via drain_inbox to our _on_trades_response consumer.
        self._executor.submit_trades_query_async(
            order_id=result.order_id,
            broker_ref=pending.broker_ref,
        )

    # ============================================
    # Consumer — runs when TradesQueryResponse arrives, compares, logs
    # ============================================

    def _on_trades_response(self, response: TradesQueryResponse) -> None:
        """
        Pop snapshot, compute broker truth from response.trades, compare.

        Pops the snapshot unconditionally so failed responses do not leak
        into self._pending_audits. Strict read-only — reads from response.trades
        (immutable), never from pending.trades (mutation-order-sensitive).

        Args:
            response: TradesQueryResponse from the worker thread
        """
        snapshot = self._pending_audits.pop(response.order_id, None)
        if snapshot is None:
            return    # Not triggered by us — another consumer or unrelated response

        if not response.success:
            self._logger.warning(
                f"[DRIFT] trades query failed for {response.order_id}, audit skipped: "
                f"{response.error_message or 'unknown'}"
            )
            return    # pop already happened — no leak

        if not response.trades:
            self._logger.warning(
                f"[DRIFT] {response.order_id} trades query returned empty list, audit skipped "
                f"(possibly broker settlement lag)"
            )
            return

        # Compute broker truth from response.trades (immutable)
        real_fee = sum(t.fee for t in response.trades)
        real_volume = sum(t.volume for t in response.trades)
        if real_volume > 0:
            real_price = sum(t.volume * t.price for t in response.trades) / real_volume
        else:
            real_price = 0.0
        real_currency = response.trades[0].fee_currency if response.trades else None

        self._summary.total_orders_audited += 1

        # FEE drift — currency-aware (skip if mismatch, surface warning)
        if snapshot.fee_currency and real_currency and snapshot.fee_currency != real_currency:
            self._logger.warning(
                f"[DRIFT] {snapshot.order_id} fee currency mismatch "
                f"(local={snapshot.fee_currency}, broker={real_currency}) — fee comparison skipped"
            )
        else:
            self._compare_and_record(
                drift_type=DriftType.FEE,
                local=snapshot.synthetic_cumulative_fee,
                broker=real_fee,
                snapshot=snapshot,
                threshold_pct=self._config.fee_threshold_pct,
                fee_currency=snapshot.fee_currency or real_currency,
            )

        # VOLUME drift — requested vs. cumulative filled
        self._compare_and_record(
            drift_type=DriftType.VOLUME,
            local=snapshot.requested_lots,
            broker=real_volume,
            snapshot=snapshot,
            threshold_pct=self._config.volume_threshold_pct,
        )

        # PRICE drift — structural on crypto trade-channel data (#244)
        self._compare_and_record(
            drift_type=DriftType.PRICE,
            local=snapshot.synthetic_cumulative_avg_price,
            broker=real_price,
            snapshot=snapshot,
            threshold_pct=self._config.price_threshold_pct,
            is_structural=True,
        )

    # ============================================
    # Comparison + Logging
    # ============================================

    def _compare_and_record(
        self,
        drift_type: DriftType,
        local: float,
        broker: float,
        snapshot: AuditContext,
        threshold_pct: float,
        is_structural: bool = False,
        fee_currency: Optional[str] = None,
    ) -> None:
        """
        Compute drift, append record, update aggregates, log if over threshold.

        Args:
            drift_type: FEE / VOLUME / PRICE
            local: Locally-computed value (synthetic)
            broker: Broker-reported truth
            snapshot: Captured outcome context
            threshold_pct: Percent threshold for the "exceeded" flag
            is_structural: True for PRICE drift — marks as #244-related
            fee_currency: Currency string (FEE drift only)
        """
        absolute_delta = abs(local - broker)
        if broker > 0:
            relative_delta_pct = absolute_delta / broker * 100.0
        elif local > 0:
            relative_delta_pct = 100.0
        else:
            relative_delta_pct = 0.0

        threshold_exceeded = relative_delta_pct > threshold_pct

        record = DriftRecord(
            timestamp=datetime.now(timezone.utc),
            order_id=snapshot.order_id,
            broker_ref=snapshot.broker_ref,
            symbol=snapshot.symbol,
            drift_type=drift_type,
            local_value=local,
            broker_value=broker,
            absolute_delta=absolute_delta,
            relative_delta_pct=relative_delta_pct,
            threshold_exceeded=threshold_exceeded,
            is_structural=is_structural,
            fee_currency=fee_currency,
        )
        self._summary.records.append(record)

        # Update aggregate counters + max
        if drift_type is DriftType.FEE:
            if threshold_exceeded:
                self._summary.fee_events += 1
            if relative_delta_pct > self._summary.max_fee_drift_pct:
                self._summary.max_fee_drift_pct = relative_delta_pct
        elif drift_type is DriftType.VOLUME:
            if threshold_exceeded:
                self._summary.volume_events += 1
            if relative_delta_pct > self._summary.max_volume_drift_pct:
                self._summary.max_volume_drift_pct = relative_delta_pct
        elif drift_type is DriftType.PRICE:
            # Structural — counter increments only when actual drift exists
            # (threshold-exceeded), keeping semantic alignment with FEE/VOLUME.
            # A zero-drift record is still appended to summary.records as
            # evidence the comparison was performed, but it does not inflate
            # the headline counter.
            if threshold_exceeded:
                self._summary.price_events += 1
            if relative_delta_pct > self._summary.max_price_drift_pct:
                self._summary.max_price_drift_pct = relative_delta_pct

        if threshold_exceeded or self._config.log_all:
            structural_tag = ' [STRUCTURAL]' if is_structural else ''
            cur_tag = f' {fee_currency}' if fee_currency else ''
            self._logger.warning(
                f"[DRIFT]{structural_tag} order={snapshot.order_id} "
                f"broker_ref={snapshot.broker_ref} symbol={snapshot.symbol}\n"
                f"   {drift_type.value.upper()}  local={local:.6f}{cur_tag}  "
                f"broker={broker:.6f}{cur_tag}  delta={relative_delta_pct:+.2f}%  "
                f"(threshold {threshold_pct}%)"
            )

    # ============================================
    # Public accessors (used by tick loop in Tranche D)
    # ============================================

    def get_summary(self) -> DriftAuditSummary:
        """Return aggregate counters + full DriftRecord history."""
        return self._summary

    def get_display_counters(self) -> Dict[str, float]:
        """
        Return slim counter dict for AutoTraderDisplayStats.drift_* population.

        Used by AutotraderTickLoop._build_display_stats to surface the Audit
        footer in the SESSION panel.
        """
        return {
            'drift_enabled': True,
            'drift_audited': self._summary.total_orders_audited,
            'drift_fee_events': self._summary.fee_events,
            'drift_volume_events': self._summary.volume_events,
            'drift_price_events': self._summary.price_events,
            'drift_max_fee_pct': self._summary.max_fee_drift_pct,
        }

    # ============================================
    # Shutdown
    # ============================================

    def shutdown(self) -> None:
        """
        Session-end cleanup.

        Warns about audits that never received a trades_query response (would
        leak memory otherwise) and emits a final summary line to the session
        log. Called from AutotraderMain._shutdown.
        """
        if self._pending_audits:
            self._logger.warning(
                f"[DRIFT] {len(self._pending_audits)} audits unfinished at shutdown: "
                f"{list(self._pending_audits.keys())}"
            )
        self._pending_audits.clear()

        self._logger.info(
            f"📊 DriftAudit final: {self._summary.total_orders_audited} orders audited | "
            f"FEE: {self._summary.fee_events} events (max {self._summary.max_fee_drift_pct:.2f}%) | "
            f"VOL: {self._summary.volume_events} events (max {self._summary.max_volume_drift_pct:.2f}%) | "
            f"PRICE: {self._summary.price_events} events (max {self._summary.max_price_drift_pct:.2f}%) "
            f"[structural — see #244]"
        )
