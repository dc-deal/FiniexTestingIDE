"""
FiniexTestingIDE - Field Study Phase Machine (#332)

Wall-clock / state-driven engine that drives the LiveFieldStudy phase sequence.
It is a pure state machine: it consumes a PhaseContext observation per tick and
emits a typed PhaseAction (submit / close / cancel / modify / end-session / none).
It performs no I/O and tracks no broker order ids — the decision logic owns broker
access, order-id bookkeeping, and event observation.

Common flow per phase:
    PENDING → SUBMIT → AWAIT_FILL → (CLOSE) → (POST_CLOSE_WAIT) → DONE
    TIMEOUT → re-arm (LIMIT_OPEN, bounded) → SUBMIT  |  else: outcome + advance
    REJECTED → expected → PASS  |  unexpected → FAIL
"""

from datetime import datetime
from typing import Callable, Dict, List, Optional

from python.framework.types.autotrader_types.field_study_types import (
    FieldStudyPhase,
    PhaseAction,
    PhaseActionKind,
    PhaseContext,
    PhaseOutcome,
    PhaseResult,
    PhaseSide,
    PhaseState,
    PhaseType,
)

# Lots-comparison epsilon for partial-close detection.
_LOTS_EPS = 1e-9


class FieldStudyPhaseMachine:
    """
    Drives the Field Study phase sequence from per-tick observations.

    Args:
        phases: Ordered, capability-filtered phase list (disabled phases skip)
        default_lot: Lot size used when a phase does not set its own
        default_limit_offset_pct: Resting-limit distance from market when a phase omits it
        max_rearm_attempts: Re-arm cap for LIMIT_OPEN phases (<=0 = unbounded:
            re-arm until fill / budget / session timeout)
        rearm_shrink_pct: Fraction the limit offset shrinks per re-arm (toward market)
    """

    def __init__(
        self,
        phases: List[FieldStudyPhase],
        default_lot: float,
        default_limit_offset_pct: float,
        max_rearm_attempts: int,
        rearm_shrink_pct: float,
    ):
        self._phases = phases
        self._default_lot = default_lot
        self._default_offset = default_limit_offset_pct
        self._max_rearm = max_rearm_attempts
        self._rearm_shrink = rearm_shrink_pct

        self._idx = 0
        self._state = PhaseState.PENDING
        self._step = 0
        self._phase_start: Optional[datetime] = None
        self._submit_time: Optional[datetime] = None
        self._rearm_attempts = 0
        self._submitted_count = 0
        self._cur_offset = default_limit_offset_pct
        self._full_lots: Optional[float] = None

        self._results: List[PhaseResult] = []

        self._handlers: Dict[PhaseType, Callable[[FieldStudyPhase, PhaseContext], PhaseAction]] = {
            PhaseType.MARKET_OPEN: self._handle_market_open,
            PhaseType.MARKET_CLOSE_ALL: self._handle_close_all,
            PhaseType.FORCE_CLOSE: self._handle_close_all,
            PhaseType.LIMIT_OPEN: self._handle_limit_open,
            PhaseType.LIMIT_MODIFY: self._handle_limit_modify,
            PhaseType.LIMIT_CANCEL: self._handle_limit_cancel,
            PhaseType.MULTI_LIMIT: self._handle_multi_limit,
            PhaseType.MULTI_CANCEL: self._handle_multi_cancel,
            PhaseType.PARTIAL_CLOSE: self._handle_partial_close,
            PhaseType.IDLE: self._handle_idle,
            PhaseType.FINAL_SUMMARY: self._handle_final,
        }

    # ============================================
    # Public surface
    # ============================================

    def advance(self, ctx: PhaseContext) -> PhaseAction:
        """
        Advance the machine by one observation tick.

        Args:
            ctx: Per-tick observation (time, prices, counts, event flags)

        Returns:
            The PhaseAction to perform this tick (NONE = wait)
        """
        if self.is_complete():
            return PhaseAction(PhaseActionKind.NONE, '', reason='all phases complete')

        phase = self._phases[self._idx]

        # Phase entry — initialize per-phase state once.
        if self._phase_start is None:
            self._phase_start = ctx.now
            self._state = PhaseState.PENDING
            self._step = 0
            self._rearm_attempts = 0
            self._submitted_count = 0
            self._full_lots = None
            self._cur_offset = (
                phase.limit_offset_pct if phase.limit_offset_pct is not None
                else self._default_offset
            )
            if not phase.enabled:
                return self._finish(phase, PhaseOutcome.SKIPPED, 'disabled', ctx.now)

        return self._handlers[phase.phase_type](phase, ctx)

    def is_complete(self) -> bool:
        """Whether every phase has been processed."""
        return self._idx >= len(self._phases)

    def get_results(self) -> List[PhaseResult]:
        """Completed phase results (oldest first) — for the recorder/certificate."""
        return list(self._results)

    def get_current_phase_id(self) -> Optional[str]:
        """The phase currently in progress, or None when complete."""
        if self.is_complete():
            return None
        return self._phases[self._idx].phase_id

    def get_current_phase_index(self) -> Optional[int]:
        """The index of the phase currently in progress, or None when complete."""
        if self.is_complete():
            return None
        return self._idx

    def get_state(self) -> PhaseState:
        """Current per-phase sub-state (PENDING/AWAIT_FILL/POST_CLOSE_WAIT/…) — for tracing."""
        return self._state

    def get_submit_time(self) -> Optional[datetime]:
        """Wall/tick time the current wait clock started (last issued op) — for tracing."""
        return self._submit_time

    # ============================================
    # Transition helpers
    # ============================================

    def _act(self, ctx: PhaseContext, action: PhaseAction) -> PhaseAction:
        """Issue an operation and (re)start the wait clock for the next observation."""
        self._submit_time = ctx.now
        return action

    def _finish(
        self,
        phase: FieldStudyPhase,
        outcome: PhaseOutcome,
        reason: str,
        now: datetime,
    ) -> PhaseAction:
        """Record the phase outcome, advance to the next phase, and wait this tick."""
        self._results.append(PhaseResult(
            phase_id=phase.phase_id,
            phase_type=phase.phase_type,
            outcome=outcome,
            reason=reason,
            started_utc=self._phase_start,
            ended_utc=now,
            rearm_attempts=self._rearm_attempts,
        ))
        self._idx += 1
        self._phase_start = None
        self._submit_time = None
        return PhaseAction(
            PhaseActionKind.NONE, phase.phase_id,
            reason=f'{phase.phase_id} → {outcome.value}: {reason}',
        )

    def _timed_out(self, phase: FieldStudyPhase, ctx: PhaseContext) -> bool:
        """Whether the current wait exceeded the phase fill timeout."""
        if self._submit_time is None:
            return False
        return (ctx.now - self._submit_time).total_seconds() > phase.fill_timeout_s

    def _lots(self, phase: FieldStudyPhase) -> float:
        """Resolve the order size for a phase (phase override or default)."""
        return phase.lots if phase.lots is not None else self._default_lot

    def _limit_price(self, phase: FieldStudyPhase, mid: float, offset: float) -> float:
        """
        Resting-limit price: below market for LONG (buy), above market for SHORT (sell).

        Args:
            phase: The phase (side)
            mid: Current mid price
            offset: Fractional distance from market

        Returns:
            Limit price
        """
        if phase.side == PhaseSide.SHORT:
            return mid * (1.0 + offset)
        return mid * (1.0 - offset)

    # ============================================
    # Phase handlers
    # ============================================

    def _handle_market_open(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.AWAIT_FILL
            return self._act(ctx, PhaseAction(
                PhaseActionKind.SUBMIT_MARKET, phase.phase_id,
                side=phase.side, lots=self._lots(phase),
                reason='market open',
            ))

        if ctx.rejected_since_submit:
            if phase.expect_rejection:
                return self._finish(phase, PhaseOutcome.EXPECTED_REJECTION, 'rejected as expected', ctx.now)
            return self._finish(phase, PhaseOutcome.FAIL, 'unexpected rejection', ctx.now)

        if ctx.filled_since_submit or ctx.open_position_count > 0:
            if phase.expect_rejection:
                return self._finish(phase, PhaseOutcome.FAIL, 'filled but rejection expected', ctx.now)
            return self._finish(phase, PhaseOutcome.PASS, 'filled', ctx.now)

        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL, 'no fill/rejection within timeout', ctx.now)

        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_close_all(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        is_force = phase.phase_type == PhaseType.FORCE_CLOSE

        if self._state == PhaseState.PENDING:
            if ctx.open_position_count == 0 and ctx.active_limit_count == 0:
                return self._finish(phase, PhaseOutcome.SKIPPED, 'already flat', ctx.now)
            self._state = PhaseState.CLOSE
            if is_force and ctx.active_limit_count > 0:
                return self._act(ctx, PhaseAction(PhaseActionKind.CANCEL_ALL, phase.phase_id, reason='force cancel resting'))
            return self._act(ctx, PhaseAction(PhaseActionKind.CLOSE_ALL, phase.phase_id, reason='close all positions'))

        flat = ctx.open_position_count == 0 and (not is_force or ctx.active_limit_count == 0)
        if flat:
            return self._finish(phase, PhaseOutcome.PASS, 'flat', ctx.now)
        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL, 'not flat within timeout', ctx.now)
        if not ctx.has_pending:
            if is_force and ctx.active_limit_count > 0:
                return self._act(ctx, PhaseAction(PhaseActionKind.CANCEL_ALL, phase.phase_id, reason='retry cancel'))
            if ctx.open_position_count > 0:
                return self._act(ctx, PhaseAction(PhaseActionKind.CLOSE_ALL, phase.phase_id, reason='retry close'))
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_limit_open(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.SUBMIT

        if self._state == PhaseState.SUBMIT:
            # A re-arm's cancel can race a fill: the "stale" limit filled instead of
            # cancelling (cancel-rejected "already filled"). If that fill already landed,
            # PASS — never submit another order, which would orphan and block later phases.
            # Guarded on _submit_time (None at phase entry) so a pre-existing position
            # never triggers a spurious PASS before this phase has placed anything.
            if self._submit_time is not None and (ctx.filled_since_submit or ctx.open_position_count > 0):
                return self._finish(phase, PhaseOutcome.PASS, 'filled', ctx.now)
            if ctx.active_limit_count > 0 or ctx.has_pending:
                return PhaseAction(PhaseActionKind.NONE, phase.phase_id)
            self._state = PhaseState.AWAIT_FILL
            price = self._limit_price(phase, ctx.mid_price, self._cur_offset)
            return self._act(ctx, PhaseAction(
                PhaseActionKind.SUBMIT_LIMIT, phase.phase_id,
                side=phase.side, lots=self._lots(phase), price=price,
                reason=f'limit rest (rearm {self._rearm_attempts}, offset {self._cur_offset:.4f})',
            ))

        # AWAIT_FILL
        if ctx.filled_since_submit or ctx.open_position_count > 0:
            return self._finish(phase, PhaseOutcome.PASS, 'filled', ctx.now)
        if ctx.rejected_since_submit:
            if phase.expect_rejection:
                return self._finish(phase, PhaseOutcome.EXPECTED_REJECTION, 'rejected as expected', ctx.now)
            return self._finish(phase, PhaseOutcome.FAIL, 'unexpected rejection', ctx.now)
        if self._timed_out(phase, ctx):
            if phase.rearm and (self._max_rearm <= 0 or self._rearm_attempts < self._max_rearm) and ctx.budget_ok:
                self._rearm_attempts += 1
                self._cur_offset *= (1.0 - self._rearm_shrink)
                self._state = PhaseState.SUBMIT
                return self._act(ctx, PhaseAction(
                    PhaseActionKind.CANCEL_ALL, phase.phase_id,
                    reason='re-arm: cancel stale limit',
                ))
            return self._finish(phase, PhaseOutcome.INCONCLUSIVE, 'not filled within re-arm budget', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_limit_modify(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.AWAIT_FILL
            price = self._limit_price(phase, ctx.mid_price, self._cur_offset)
            return self._act(ctx, PhaseAction(
                PhaseActionKind.SUBMIT_LIMIT, phase.phase_id,
                side=phase.side, lots=self._lots(phase), price=price,
                reason='limit rest (pre-modify)',
            ))

        if ctx.filled_since_submit or ctx.open_position_count > 0:
            return self._finish(phase, PhaseOutcome.PASS, 'filled', ctx.now)
        if ctx.rejected_since_submit:
            return self._finish(phase, PhaseOutcome.FAIL, 'rejected', ctx.now)

        if self._step == 0 and ctx.active_limit_count >= 1:
            self._step = 1
            near = self._limit_price(phase, ctx.mid_price, self._cur_offset * 0.25)
            return self._act(ctx, PhaseAction(
                PhaseActionKind.MODIFY_LIMIT, phase.phase_id, price=near,
                reason='modify limit toward market',
            ))

        if self._timed_out(phase, ctx):
            if self._step == 0:
                # Order never rested at the broker → mechanical failure.
                return self._finish(phase, PhaseOutcome.FAIL, 'never rested', ctx.now)
            # Modified, but the market never reached it → market-dependent, not a failure.
            return self._finish(phase, PhaseOutcome.INCONCLUSIVE, 'no fill after modify', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_limit_cancel(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.AWAIT_FILL
            price = self._limit_price(phase, ctx.mid_price, self._cur_offset)
            return self._act(ctx, PhaseAction(
                PhaseActionKind.SUBMIT_LIMIT, phase.phase_id,
                side=phase.side, lots=self._lots(phase), price=price,
                reason='limit rest (to cancel)',
            ))

        if self._state == PhaseState.AWAIT_FILL:
            if ctx.filled_since_submit or ctx.open_position_count > 0:
                return self._finish(phase, PhaseOutcome.FAIL, 'filled before cancel', ctx.now)
            if ctx.active_limit_count >= 1:
                self._state = PhaseState.POST_CLOSE_WAIT
                return self._act(ctx, PhaseAction(PhaseActionKind.CANCEL, phase.phase_id, reason='cancel resting limit'))
            if self._timed_out(phase, ctx):
                return self._finish(phase, PhaseOutcome.FAIL, 'never rested', ctx.now)
            return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

        # POST_CLOSE_WAIT
        if ctx.active_limit_count == 0 and ctx.open_position_count == 0:
            return self._finish(phase, PhaseOutcome.PASS, 'cancelled, no position', ctx.now)
        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL, 'cancel not confirmed', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_multi_limit(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.SUBMIT

        if self._state == PhaseState.SUBMIT:
            if self._submitted_count < phase.order_count:
                if ctx.has_pending:
                    return PhaseAction(PhaseActionKind.NONE, phase.phase_id)
                offset = self._cur_offset * (1.0 + 0.5 * self._submitted_count)
                price = self._limit_price(phase, ctx.mid_price, offset)
                self._submitted_count += 1
                return self._act(ctx, PhaseAction(
                    PhaseActionKind.SUBMIT_LIMIT, phase.phase_id,
                    side=phase.side, lots=self._lots(phase), price=price,
                    reason=f'multi limit {self._submitted_count}/{phase.order_count}',
                ))
            self._state = PhaseState.AWAIT_FILL

        if ctx.active_limit_count >= phase.order_count:
            return self._finish(phase, PhaseOutcome.PASS, 'all watching', ctx.now)
        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL, 'not all resting', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_multi_cancel(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            if ctx.active_limit_count == 0:
                return self._finish(phase, PhaseOutcome.SKIPPED, 'nothing to cancel', ctx.now)
            self._state = PhaseState.POST_CLOSE_WAIT
            return self._act(ctx, PhaseAction(PhaseActionKind.CANCEL_ALL, phase.phase_id, reason='cancel all resting'))

        if ctx.active_limit_count == 0:
            return self._finish(phase, PhaseOutcome.PASS, 'all cancelled', ctx.now)
        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL, 'cancel-all not confirmed', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_partial_close(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.AWAIT_FILL
            return self._act(ctx, PhaseAction(
                PhaseActionKind.SUBMIT_MARKET, phase.phase_id,
                side=phase.side, lots=self._lots(phase),
                reason='partial-close: open',
            ))

        if self._state == PhaseState.AWAIT_FILL:
            if ctx.rejected_since_submit:
                return self._finish(phase, PhaseOutcome.FAIL, 'open rejected', ctx.now)
            if ctx.filled_since_submit or ctx.open_position_count > 0:
                self._full_lots = ctx.current_position_lots
                self._state = PhaseState.CLOSE
                return self._act(ctx, PhaseAction(
                    PhaseActionKind.CLOSE_PARTIAL, phase.phase_id, close_fraction=0.5,
                    reason='close 50%',
                ))
            if self._timed_out(phase, ctx):
                return self._finish(phase, PhaseOutcome.FAIL, 'open not filled', ctx.now)
            return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

        if self._state == PhaseState.CLOSE:
            reduced = (
                self._full_lots is not None
                and ctx.current_position_lots is not None
                and ctx.current_position_lots < self._full_lots - _LOTS_EPS
            )
            if reduced:
                self._state = PhaseState.POST_CLOSE_WAIT
                return self._act(ctx, PhaseAction(PhaseActionKind.CLOSE_ALL, phase.phase_id, reason='close remainder'))
            if self._timed_out(phase, ctx):
                return self._finish(phase, PhaseOutcome.FAIL, 'partial close not observed', ctx.now)
            return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

        # POST_CLOSE_WAIT
        if ctx.open_position_count == 0:
            return self._finish(phase, PhaseOutcome.PASS, 'half then flat', ctx.now)
        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL, 'remainder not closed', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_idle(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.POST_FILL_WAIT
            self._submit_time = ctx.now
            return PhaseAction(PhaseActionKind.NONE, phase.phase_id, reason='idle heartbeat')
        if (ctx.now - self._submit_time).total_seconds() >= phase.idle_seconds:
            return self._finish(phase, PhaseOutcome.PASS, 'idle complete', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_final(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        self._results.append(PhaseResult(
            phase_id=phase.phase_id,
            phase_type=phase.phase_type,
            outcome=PhaseOutcome.PASS,
            reason='session end requested',
            started_utc=self._phase_start,
            ended_utc=ctx.now,
        ))
        self._idx += 1
        self._phase_start = None
        self._submit_time = None
        return PhaseAction(PhaseActionKind.END_SESSION, phase.phase_id, reason='field study complete')
