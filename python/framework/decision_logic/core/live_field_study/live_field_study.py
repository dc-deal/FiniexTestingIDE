"""
FiniexTestingIDE - Live Field Study Decision Logic (#332, CORE)

Operator-driven acceptance-test decision logic: drives the full live pipeline
through a deterministic, wall-clock phase sequence (every order type, modify/cancel
path, rejection battery, partial close, idle heartbeat) against a real broker. It is
the live equivalent of the plan-driven BacktestingMarginStress.

The phase logic lives in FieldStudyPhaseMachine (a pure state machine). This class is
the AbstractDecisionLogic adapter: it builds the per-tick observation, asks the machine
for the next PhaseAction, dispatches that action via DecisionTradingApi, and feeds
order/lifecycle events (#348) back as observations. Submits route through the standard
Decision action so the safety circuit breaker can suppress them; closes/cancels are not
suppressed. Phase 19 ends the session via the existing request_session_end API (#348).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.decision_logic.core.live_field_study.field_study_phase_machine import FieldStudyPhaseMachine
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.field_study_recorder import FieldStudyRecorder
from python.framework.types.autotrader_types.field_study_types import (
    FieldStudyPhase,
    PhaseAction,
    PhaseActionKind,
    PhaseContext,
    PhaseResult,
    PhaseSide,
    PhaseType,
)
from python.framework.types.decision_event_types import (
    DecisionEventType,
    OrderCancelledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    PartialCloseEvent,
    SessionEndSeverity,
)
from python.framework.types.decision_logic_types import AwarenessLevel, Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.trading_env_types.order_types import OrderResult, OrderSide, OrderType
from python.framework.types.worker_types import WorkerResult

# Limit-family phases require broker LIMIT support — auto-skipped on adapters without it.
_LIMIT_FAMILY = frozenset({
    PhaseType.LIMIT_OPEN,
    PhaseType.LIMIT_MODIFY,
    PhaseType.LIMIT_CANCEL,
    PhaseType.MULTI_LIMIT,
    PhaseType.MULTI_CANCEL,
})


class LiveFieldStudy(AbstractDecisionLogic):
    """
    Deterministic live acceptance-test decision logic (#332).

    Configuration (decision_logic_config):
        phase_sequence: List of phase specs (see FieldStudyPhase.from_dict)
        lot_size: Default order size when a phase omits its own
        limit_offset_pct: Default resting-limit distance from market (fraction)
        max_session_cost_usd: Session realized-cost ceiling (0 = disabled; enforced in #332 Chunk D)
        max_rearm_attempts: Re-arm cap for LIMIT_OPEN phases
        rearm_offset_pct: Fraction the limit offset shrinks per re-arm (toward market)
        session_timeout_s: Hard session wall-clock cap (enforced in #332 Chunk D)
        halt_after_phase: Stop cleanly after the named phase id (step mode)
    """

    def __init__(
        self,
        name: str,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None,
    ):
        super().__init__(name, logger, config, trading_context=trading_context)

        raw_sequence: List[Dict[str, Any]] = self.params.get('phase_sequence')
        self._phases: List[FieldStudyPhase] = [
            FieldStudyPhase.from_dict(raw) for raw in raw_sequence
        ]
        self._default_lot = self.params.get('lot_size')
        self._limit_offset_pct = self.params.get('limit_offset_pct')
        self._max_session_cost_usd = self.params.get('max_session_cost_usd')
        self._max_rearm_attempts = self.params.get('max_rearm_attempts')
        self._rearm_offset_pct = self.params.get('rearm_offset_pct')
        self._session_timeout_s = self.params.get('session_timeout_s')
        self._halt_after_phase = self.params.get('halt_after_phase')

        # Built lazily on first compute — capability filtering needs the trading API.
        self._machine: Optional[FieldStudyPhaseMachine] = None
        self._pending_action: Optional[PhaseAction] = None

        # JSONL recorder (bot plane) — injected + lifecycle-owned by the AutoTrader wiring.
        self._recorder: Optional[FieldStudyRecorder] = None

        # Session guards (budget + wall-clock) — enforced on every machine advance.
        self._realized_cost = 0.0
        self._session_started_at: Optional[datetime] = None
        self._safe_abort_active = False
        self._abort_reason = ''

        # Resting limit order ids of the current phase (for cancel / modify).
        self._phase_order_ids: List[str] = []
        self._last_phase_id: Optional[str] = None
        self._logged_results: int = 0

        # Symbol + last-known mid — resolved from the trading context at init and
        # refreshed on each real tick. Used on a ghost-pass (tick=None) where no
        # fresh market data is available (#360).
        self._symbol: Optional[str] = (
            trading_context.symbol if trading_context is not None else None
        )
        self._last_mid: Optional[float] = None

        # Diagnostic trace state (#360/#13/#15 forensics) — DEBUG emits only on
        # change (state / active-limit count / action / phase); VERBOSE = per-advance.
        self._last_trace_state = None
        self._last_trace_limits: int = -1

        # Event observation flags (set by the #348 hooks, reset on each new submit / phase).
        self._filled_flag = False
        self._rejected_flag = False
        self._cancelled_flag = False

        self.logger.info(
            f"LiveFieldStudy initialized: {len(self._phases)} phases, "
            f"lot_size={self._default_lot}, limit_offset={self._limit_offset_pct}, "
            f"max_rearm={self._max_rearm_attempts}, "
            f"budget=${self._max_session_cost_usd}"
        )

    # ============================================
    # Factory interface
    # ============================================

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        # MARKET is always needed; LIMIT only if the configured phase sequence uses a
        # limit phase — so a limit-free sequence (e.g. the mock dress-rehearsal) can run
        # against a MARKET-only adapter.
        types = [OrderType.MARKET]
        limit_phase_types = {
            PhaseType.LIMIT_OPEN, PhaseType.LIMIT_MODIFY, PhaseType.LIMIT_CANCEL,
            PhaseType.MULTI_LIMIT, PhaseType.MULTI_CANCEL,
        }
        for raw in decision_logic_config.get('phase_sequence', []):
            try:
                phase_type = PhaseType(raw.get('phase_type'))
            except ValueError:
                continue
            if phase_type in limit_phase_types:
                types.append(OrderType.LIMIT)
                break
        return types

    def get_required_worker_instances(self) -> Dict[str, str]:
        # The phase sequence is wall-clock / state driven and ignores indicator
        # output, but the worker pipeline requires at least one worker — carry a
        # single minimal CORE worker purely for pipeline compatibility (unused).
        return {'pipeline_rsi': 'CORE/rsi'}

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        return {
            'phase_sequence': InputParamDef(
                param_type=list, default=[],
                description='Ordered list of Field Study phase specs',
            ),
            'lot_size': InputParamDef(
                param_type=float, default=0.01, min_val=0.0,
                description='Default order size (min-lot for the Field Study)',
            ),
            'limit_offset_pct': InputParamDef(
                param_type=float, default=0.002, min_val=0.0, max_val=1.0,
                description='Default resting-limit distance from market (fraction)',
            ),
            'max_session_cost_usd': InputParamDef(
                param_type=float, default=0.0, min_val=0.0,
                description='Session realized-cost ceiling (0 = disabled)',
            ),
            'max_rearm_attempts': InputParamDef(
                param_type=int, default=6, min_val=0,
                description='Re-arm cap for LIMIT_OPEN phases (0 = unbounded: re-arm until fill / budget / session timeout)',
            ),
            'rearm_offset_pct': InputParamDef(
                param_type=float, default=0.25, min_val=0.0, max_val=1.0,
                description='Fraction the limit offset shrinks per re-arm',
            ),
            'session_timeout_s': InputParamDef(
                param_type=float, default=1800.0, min_val=0.0,
                description='Hard session wall-clock cap (seconds)',
            ),
            'halt_after_phase': InputParamDef(
                param_type=str, default='',
                description='Stop cleanly after the named phase id (step mode)',
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        return {
            'phase_id': OutputParamDef(
                param_type=str, description='Current phase id', category='INFO',
            ),
            'phase_action': OutputParamDef(
                param_type=str, description='Action issued this tick', category='INFO',
            ),
            'reason': OutputParamDef(
                param_type=str, description='Human-readable phase narration', category='INFO',
            ),
            'price': OutputParamDef(
                param_type=float, min_val=0.0, description='Price at decision time', category='INFO',
            ),
        }

    # ============================================
    # Decision Event Channel (#348)
    # ============================================

    @classmethod
    def get_subscribed_events(cls) -> Set[DecisionEventType]:
        return {
            DecisionEventType.ORDER_FILLED,
            DecisionEventType.ORDER_REJECTED,
            DecisionEventType.ORDER_CANCELLED,
            DecisionEventType.PARTIAL_CLOSE,
        }

    def wants_heartbeat(self) -> bool:
        # The phase machine is wall-clock / state driven — it must advance between
        # ticks (cancel-confirm, phase-advance, re-arm) on the idle heartbeat (#360),
        # not only when a real market tick arrives.
        return True

    def on_order_filled(self, event: OrderFilledEvent) -> None:
        self._filled_flag = True
        self._realized_cost += event.result.commission
        if self._recorder:
            self._recorder.record_order_event(
                'order_filled', order_id=event.order_id, side=event.direction.name,
                lots=event.lots, price=event.fill_price, status='filled',
                slippage={'slippage_points': event.result.slippage_points},
                extra={'commission': event.result.commission},
            )

    def on_order_rejected(self, event: OrderRejectedEvent) -> None:
        self._rejected_flag = True
        if self._recorder:
            self._recorder.record_order_event(
                'order_rejected', order_id=event.order_id, side=event.direction.name,
                status='rejected',
                extra={
                    'reason': event.reason.value if event.reason else None,
                    'message': event.message,
                },
            )

    def on_order_cancelled(self, event: OrderCancelledEvent) -> None:
        self._cancelled_flag = True
        if self._recorder:
            self._recorder.record_order_event(
                'order_cancelled', order_id=event.order_id,
                side=event.direction.name if event.direction else None,
                status='cancelled',
            )

    def on_partial_close(self, event: PartialCloseEvent) -> None:
        # Lots-polling drives the machine; the event is recorded for the analysis stream.
        self._realized_cost += event.result.commission
        if self._recorder:
            self._recorder.record_order_event(
                'partial_close', order_id=event.position_id, side=event.direction.name,
                lots=event.closed_lots, price=event.fill_price, status='partial_close',
                extra={'remaining_lots': event.remaining_lots, 'commission': event.result.commission},
            )

    # ============================================
    # Core: compute()/compute_heartbeat() drive the machine, execute() dispatches the action
    # ============================================

    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        self._symbol = tick.symbol
        self._last_mid = (tick.bid + tick.ask) / 2.0
        return self._advance_phase_machine(is_ghost=False)

    def compute_heartbeat(
        self,
        worker_results: Dict[str, WorkerResult],
    ) -> Optional[Decision]:
        # Ghost-pass (#360): drive the phase machine from last-known market
        # state. A heartbeat that lands before the first real tick has no
        # market data — emit the idle marker instead of advancing.
        if self._last_mid is None:
            return Decision(
                action=DecisionLogicAction.FLAT,
                outputs={'phase_id': 'heartbeat', 'phase_action': 'none',
                         'reason': 'heartbeat before first tick', 'price': 0.0},
            )
        return self._advance_phase_machine(is_ghost=True)

    def _advance_phase_machine(self, is_ghost: bool) -> Decision:
        """
        Drive the phase machine one advance — shared by both pass-triggers.

        Args:
            is_ghost: True on a heartbeat ghost-pass (trace source tagging)

        Returns:
            Decision carrying the phase action
        """
        self._ensure_machine()
        mid = self._last_mid
        symbol = self._symbol

        now = self.trading_api.get_current_time()
        if self._session_started_at is None:
            self._session_started_at = now

        # Session guards — safe-abort once on a budget or wall-clock breach.
        if not self._safe_abort_active and self._session_guard_breached(now):
            self._trigger_safe_abort()
        if self._safe_abort_active:
            self._close_all()
            return Decision(
                action=DecisionLogicAction.FLAT,
                outputs={'phase_id': 'aborted', 'phase_action': 'close_all',
                         'reason': self._abort_reason, 'price': mid},
            )

        positions = self.trading_api.get_open_positions(symbol)
        counts = self.trading_api.get_active_order_counts()

        ctx = PhaseContext(
            now=now,
            mid_price=mid,
            open_position_count=len(positions),
            active_limit_count=counts.get('active_limits', 0),
            has_pending=self.trading_api.has_pipeline_orders(),
            filled_since_submit=self._filled_flag,
            rejected_since_submit=self._rejected_flag,
            cancelled_since_submit=self._cancelled_flag,
            current_position_lots=positions[0].lots if positions else None,
            budget_ok=self._budget_ok(),
        )

        action = self._machine.advance(ctx)
        self._pending_action = action
        cur_phase = self._machine.get_current_phase_id()

        # Diagnostic trace (#13/#15 forensics): per-advance, tick vs ghost source.
        self._trace_advance(is_ghost, ctx, action, cur_phase)

        self._log_new_results()

        if cur_phase != self._last_phase_id:
            # New phase — drop stale resting-order ids, reset flags, mark the boundary.
            self._phase_order_ids = []
            self._reset_flags()
            self._record_phase_start(cur_phase)
            self._last_phase_id = cur_phase
            self.logger.debug(
                f"[FS_RESTING] event=phase_start phase={cur_phase} "
                f"{self._resting_snapshot_str()}")

        # Submits route through the standard BUY/SELL action so the safety circuit
        # breaker can suppress them (it overrides BUY/SELL → FLAT when blocked).
        if action.kind in (PhaseActionKind.SUBMIT_MARKET, PhaseActionKind.SUBMIT_LIMIT):
            dl_action = (
                DecisionLogicAction.BUY if action.side == PhaseSide.LONG
                else DecisionLogicAction.SELL
            )
        else:
            dl_action = DecisionLogicAction.FLAT

        return Decision(
            action=dl_action,
            outputs={
                'phase_id': action.phase_id,
                'phase_action': action.kind.value,
                'reason': action.reason,
                'price': action.price if action.price is not None else mid,
            },
        )

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: Optional[TickData],
    ) -> Optional[OrderResult]:
        if not self.trading_api or self._pending_action is None:
            return None

        action = self._pending_action

        if action.kind in (PhaseActionKind.SUBMIT_MARKET, PhaseActionKind.SUBMIT_LIMIT):
            # Respect a safety override: BUY/SELL turned to FLAT means the circuit
            # breaker blocked new entries — skip the submit (the machine times out).
            if decision.action not in (DecisionLogicAction.BUY, DecisionLogicAction.SELL):
                self.logger.warning(
                    f"Field Study submit suppressed by safety circuit breaker "
                    f"(phase {action.phase_id})"
                )
                return None
            return self._submit(action)

        if action.kind == PhaseActionKind.CLOSE_ALL:
            self._close_all()
            return None

        if action.kind == PhaseActionKind.CLOSE_PARTIAL:
            self._close_partial(action)
            return None

        if action.kind in (PhaseActionKind.CANCEL, PhaseActionKind.CANCEL_ALL):
            self._cancel_resting()
            return None

        if action.kind == PhaseActionKind.MODIFY_LIMIT:
            self._modify_resting(action)
            return None

        if action.kind == PhaseActionKind.END_SESSION:
            self.trading_api.request_session_end(
                'field study complete', SessionEndSeverity.NORMAL
            )
            return None

        return None

    # ============================================
    # Action dispatch helpers
    # ============================================

    def _submit(self, action: PhaseAction) -> Optional[OrderResult]:
        side = OrderSide.BUY if action.side == PhaseSide.LONG else OrderSide.SELL
        order_type = (
            OrderType.MARKET if action.kind == PhaseActionKind.SUBMIT_MARKET
            else OrderType.LIMIT
        )
        # Fresh await window before submitting — a stale event from a prior phase
        # must not leak into this phase's observation.
        self._reset_flags()
        result = self.trading_api.send_order(
            symbol=self._symbol,
            order_type=order_type,
            side=side,
            lots=action.lots,
            price=action.price,
            comment=f'FieldStudy {action.phase_id}',
        )
        if result is not None and result.is_rejected:
            # Synchronous rejection (invalid lot, immediate broker reject) — the #348
            # channel only carries async outcomes, so surface it to the machine directly.
            self._rejected_flag = True
        elif order_type == OrderType.LIMIT and result is not None and result.order_id:
            self._phase_order_ids.append(result.order_id)
        # Submit trace — pins which order each phase actually placed (#13/#15 forensics).
        oid = result.order_id if result is not None else None
        status = result.status.value if result is not None and result.status else 'none'
        self.logger.debug(
            f"[FS_SUBMIT] phase={action.phase_id} type={order_type.value} side={side.value} "
            f"lots={action.lots} price={action.price} order_id={oid} status={status}")
        return result

    def _close_all(self) -> None:
        for pos in self.trading_api.get_open_positions(self._symbol):
            if not self.trading_api.is_pending_close(pos.position_id):
                self.trading_api.close_position(pos.position_id)

    def _close_partial(self, action: PhaseAction) -> None:
        positions = self.trading_api.get_open_positions(self._symbol)
        if not positions:
            return
        pos = positions[0]
        if self.trading_api.is_pending_close(pos.position_id):
            return
        fraction = action.close_fraction if action.close_fraction is not None else 0.5
        self.trading_api.close_position(pos.position_id, lots=pos.lots * fraction)

    def _cancel_resting(self) -> None:
        # Cancel the orders ACTUALLY resting at the broker, not a per-phase id
        # list. The phase machine re-issues CANCEL_ALL each tick until
        # active_limit_count reaches 0, so a cancel that returns False (order
        # still submit-in-flight) is retried naturally on the next call — never
        # dropped. This also makes force_close a true safety-net: it clears every
        # resting order, including any leaked from an earlier phase.
        resting = self.trading_api.get_active_orders()
        self.logger.debug(
            f"[FS_CANCEL] event=cancel_all {self._resting_snapshot_str(resting)}")
        for order in resting:
            scheduled = self.trading_api.cancel_limit_order(order.pending_order_id)
            # scheduled=0 with ref=NONE → the cancel was dropped (broker_ref in-flight);
            # limit_cancel/multi_cancel do NOT re-issue, so this order orphans (#13/#15).
            self.logger.debug(
                f"[FS_CANCEL] order={order.pending_order_id} ref={order.broker_ref or 'NONE'} "
                f"op={order.execution_state.in_flight_operation.name} q={int(order.execution_state.in_flight_query)} "
                f"scheduled={int(scheduled)}")
        self._phase_order_ids = []

    def _modify_resting(self, action: PhaseAction) -> None:
        if self._phase_order_ids and action.price is not None:
            self.trading_api.modify_limit_order(
                order_id=self._phase_order_ids[-1], price=action.price
            )

    # ============================================
    # Diagnostic trace (#13/#15 forensics — machine-parseable key=value)
    # ============================================

    def _resting_snapshot_str(self, orders: Optional[List[PendingOrder]] = None) -> str:
        """
        Compact snapshot of the currently resting orders (id | broker_ref | in-flight op
        | in-flight query). A phase that fails with n>0 here is leaking/orphaning an order.

        Args:
            orders: Pre-fetched resting orders (re-queried when None)

        Returns:
            Machine-parseable 'n=K orders=[id|ref=…|op=…|q=…,…]' string
        """
        if orders is None:
            orders = self.trading_api.get_active_orders()
        parts = [
            f"{o.pending_order_id}|ref={o.broker_ref or 'NONE'}"
            f"|op={o.execution_state.in_flight_operation.name}|q={int(o.execution_state.in_flight_query)}"
            for o in orders
        ]
        return f"n={len(orders)} orders=[{','.join(parts)}]"

    def _trace_advance(
        self,
        is_ghost: bool,
        ctx: PhaseContext,
        action: PhaseAction,
        cur_phase: Optional[str],
    ) -> None:
        """
        Emit one machine-parseable phase-advance trace line per advance (#13/#15 forensics).

        VERBOSE: every advance (tick AND ghost) — the firehose.
        DEBUG: only when something changes (state / active-limit count / non-NONE action /
        phase) — controlled, no per-pass spam.

        Args:
            is_ghost: True on a heartbeat ghost-pass, False on a tick pass
            ctx: The PhaseContext the machine just consumed
            action: The PhaseAction the machine returned
            cur_phase: Current phase id after the advance
        """
        src = 'ghost' if is_ghost else 'tick'
        state = self._machine.get_state()
        submit_time = self._machine.get_submit_time()
        age = f"{(ctx.now - submit_time).total_seconds():.1f}" if submit_time else "-1"
        idx = self._machine.get_current_phase_index()
        line = (
            f"[FS_PHASE] src={src} phase={cur_phase or 'done'} idx={idx} "
            f"state={state.name} action={action.kind.value} submit_age_s={age} "
            f"active_limits={ctx.active_limit_count} open_pos={ctx.open_position_count} "
            f"has_pending={int(ctx.has_pending)} filled={int(ctx.filled_since_submit)} "
            f"rejected={int(ctx.rejected_since_submit)} cancelled={int(ctx.cancelled_since_submit)} "
            f"now={ctx.now.isoformat()}"
        )
        self.logger.verbose(line)

        changed = (
            state != self._last_trace_state
            or ctx.active_limit_count != self._last_trace_limits
            or action.kind != PhaseActionKind.NONE
            or cur_phase != self._last_phase_id
        )
        if changed:
            self.logger.debug(line)
        self._last_trace_state = state
        self._last_trace_limits = ctx.active_limit_count

    # ============================================
    # Internal helpers
    # ============================================

    def _ensure_machine(self) -> None:
        """Build the phase machine on first use, after capability filtering."""
        if self._machine is not None:
            return

        caps = self.trading_api.get_order_capabilities()
        limit_supported = caps.supports_order_type(OrderType.LIMIT)
        for phase in self._phases:
            if phase.phase_type in _LIMIT_FAMILY and not limit_supported:
                phase.enabled = False
                self.logger.info(
                    f"Field Study phase '{phase.phase_id}' auto-skipped "
                    f"(broker lacks LIMIT support)"
                )

        self._machine = FieldStudyPhaseMachine(
            phases=self._phases,
            default_lot=self._default_lot,
            default_limit_offset_pct=self._limit_offset_pct,
            max_rearm_attempts=self._max_rearm_attempts,
            rearm_shrink_pct=self._rearm_offset_pct,
        )

    def _reset_flags(self) -> None:
        self._filled_flag = False
        self._rejected_flag = False
        self._cancelled_flag = False

    def _record_phase_start(self, phase_id: Optional[str]) -> None:
        """Record a phase-start marker and switch the recorder's phase context."""
        if self._recorder is None or phase_id is None:
            return
        idx = self._machine.get_current_phase_index()
        if idx is None:
            return
        side = self._phases[idx].side
        side_str = side.name if side != PhaseSide.NONE else None
        self._recorder.record_phase_start(phase_id, idx, side_str)

    def _budget_ok(self) -> bool:
        """Whether realized session cost is still under the ceiling (re-arm gate)."""
        if self._max_session_cost_usd <= 0.0:
            return True
        return self._realized_cost < self._max_session_cost_usd

    def _session_guard_breached(self, now: datetime) -> bool:
        """Whether the wall-clock or budget ceiling has been breached (→ safe-abort)."""
        if self._session_timeout_s > 0.0 and self._session_started_at is not None:
            if (now - self._session_started_at).total_seconds() > self._session_timeout_s:
                self._abort_reason = 'session wall-clock timeout'
                return True
        if self._max_session_cost_usd > 0.0 and self._realized_cost >= self._max_session_cost_usd:
            self._abort_reason = (
                f'budget exceeded (${self._realized_cost:.4f} ≥ ${self._max_session_cost_usd:.4f})'
            )
            return True
        return False

    def _trigger_safe_abort(self) -> None:
        """Cancel resting orders, close all positions, and request a graceful session end."""
        self.logger.warning(f"⛔ Field Study safe-abort: {self._abort_reason}")
        self.emit_event(
            f"safe-abort: {self._abort_reason}",
            level=AwarenessLevel.ALERT, reason_key='field_study_abort',
        )
        self._cancel_resting()
        self._close_all()
        self.trading_api.request_session_end(
            f'field study safe-abort: {self._abort_reason}', SessionEndSeverity.NORMAL
        )
        self._safe_abort_active = True

    def _log_new_results(self) -> None:
        """Emit an event for each newly completed phase; honor step mode (halt_after_phase)."""
        results = self._machine.get_results()
        total = len(self._phases)
        # results are 1:1 with phases in sequence order → list index == phase number
        for idx, result in enumerate(results[self._logged_results:], start=self._logged_results):
            if result.outcome.value == 'fail':
                level = AwarenessLevel.ALERT
            elif result.outcome.value == 'inconclusive':
                level = AwarenessLevel.NOTICE
            else:
                level = AwarenessLevel.INFO
            self.emit_event(
                f"phase ({idx + 1}/{total}) {result.phase_id} → {result.outcome.value}: {result.reason}",
                level=level,
                reason_key='field_study_phase',
            )
            # Resting-order snapshot at phase end — a fail with n>0 is the orphan/leak.
            self.logger.debug(
                f"[FS_RESTING] event=phase_end phase={result.phase_id} "
                f"outcome={result.outcome.value} {self._resting_snapshot_str()}")
            if self._recorder:
                self._recorder.record_phase_result(result)
            if self._halt_after_phase and result.phase_id == self._halt_after_phase:
                self.logger.info(
                    f"halt_after_phase '{self._halt_after_phase}' reached — ending session"
                )
                self.trading_api.request_session_end(
                    f'halt_after_phase {self._halt_after_phase}', SessionEndSeverity.NORMAL
                )
        self._logged_results = len(results)

    # ============================================
    # Public access for tests / recorder
    # ============================================

    def set_recorder(self, recorder: FieldStudyRecorder) -> None:
        """Inject the JSONL recorder (lifecycle owned + closed by the AutoTrader wiring)."""
        self._recorder = recorder

    def get_phase_ids(self) -> List[str]:
        """Ordered phase ids in the configured sequence (for the recorder header)."""
        return [p.phase_id for p in self._phases]

    def get_realized_cost(self) -> float:
        """Accumulated realized cost (sum of fill commissions) so far."""
        return self._realized_cost

    def get_phase_results(self) -> List[PhaseResult]:
        """Completed phase results (for the recorder / certificate / tests)."""
        if self._machine is None:
            return []
        return self._machine.get_results()
