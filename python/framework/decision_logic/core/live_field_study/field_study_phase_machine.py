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

from enum import StrEnum
from typing import Dict, List, Optional

from python.framework.acceptance_studies.abstract_study_phase_machine import (
    AbstractStudyPhaseMachine,
    PhaseHandler,
)
from python.framework.types.acceptance_study_types.field_study_types import (
    FieldStudyPhase,
    PhaseType,
)
from python.framework.types.acceptance_study_types.study_phase_types import (
    PhaseAction,
    PhaseActionKind,
    PhaseContext,
    PhaseOutcome,
    PhaseSide,
    PhaseState,
)
from python.framework.types.decision_event_types import SessionEndSeverity

# Lots-comparison epsilon for partial-close detection.
_LOTS_EPS = 1e-9


class FieldStudyPhaseMachine(AbstractStudyPhaseMachine):
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
        super().__init__(phases)
        self._default_lot = default_lot
        self._default_offset = default_limit_offset_pct
        self._max_rearm = max_rearm_attempts
        self._rearm_shrink = rearm_shrink_pct

        self._step = 0
        self._submitted_count = 0
        self._cur_offset = default_limit_offset_pct
        self._full_lots: Optional[float] = None

    def _build_handlers(self) -> Dict[StrEnum, PhaseHandler]:
        return {
            PhaseType.MARKET_OPEN: self._handle_market_open,
            PhaseType.MARKET_CLOSE_ALL: self._handle_close_all,
            PhaseType.FORCE_CLOSE: self._handle_close_all,
            PhaseType.LIMIT_OPEN: self._handle_limit_open,
            PhaseType.LIMIT_MODIFY: self._handle_limit_modify,
            PhaseType.LIMIT_CANCEL: self._handle_limit_cancel,
            PhaseType.STOP_CANCEL: self._handle_stop_cancel,
            PhaseType.MULTI_LIMIT: self._handle_multi_limit,
            PhaseType.MULTI_CANCEL: self._handle_multi_cancel,
            PhaseType.PARTIAL_CLOSE: self._handle_partial_close,
            PhaseType.IDLE: self._handle_idle,
            PhaseType.CLOSE_AND_END: self._handle_close_and_end,
            PhaseType.FINAL_SUMMARY: self._handle_final,
        }

    def _on_phase_entry(self, phase: FieldStudyPhase) -> None:
        self._step = 0
        self._submitted_count = 0
        self._full_lots = None
        self._cur_offset = (
            phase.limit_offset_pct if phase.limit_offset_pct is not None
            else self._default_offset
        )

    # ============================================
    # Transition helpers
    # ============================================

    def _lots(self, phase: FieldStudyPhase) -> float:
        """Resolve the order size for a phase (phase override or default)."""
        return phase.lots if phase.lots is not None else self._default_lot

    def _stop_price(self, phase: FieldStudyPhase, mid: float, offset: float) -> float:
        """
        Resting-stop trigger: ABOVE market for LONG (buy stop), below for SHORT.

        The mirror of _limit_price, and the mirroring is the point — a stop is a breakout
        entry, so the side that makes a limit rest is the side that would make a stop fire
        immediately. Kraken does not refuse a mis-sided trigger, it executes it as a market
        order (and our simulation does the same), so getting this backwards would turn a
        resting-order phase into an unintended real fill.

        Args:
            phase: The phase (side)
            mid: Current mid price
            offset: Fractional distance from market

        Returns:
            Trigger price
        """
        if phase.side == PhaseSide.SHORT:
            return mid * (1.0 - offset)
        return mid * (1.0 + offset)

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

        if ctx.unaccounted_since_submit:
            return self._finish_unaccounted(phase, ctx)
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
        return self._close_out(phase, ctx, is_force=phase.phase_type == PhaseType.FORCE_CLOSE)

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
        if ctx.unaccounted_since_submit:
            return self._finish_unaccounted(phase, ctx)
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
        if ctx.unaccounted_since_submit:
            return self._finish_unaccounted(phase, ctx)
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

    def _handle_stop_cancel(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        """
        Place a resting STOP at the venue, confirm it rests, cancel it (#500).

        The stop mirror of _handle_limit_cancel, and it is the phase that puts the live stop
        path under real money: the payload's trigger mapping (Kraken's `price` carries the
        trigger, not a limit), the submit-response route that confirms the broker reference,
        the poll loop reading the stop world, and the cancel path that has to find the order
        in that world rather than among the limits. None of it can be proven by a mock, and
        a fill is not needed to prove it — an accepted, resting, then cancelled stop
        exercises every one of those and costs no fee at all.

        Args:
            phase: The phase being driven
            ctx: This tick's observation

        Returns:
            The action to perform
        """
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.AWAIT_FILL
            price = self._stop_price(phase, ctx.mid_price, self._cur_offset)
            return self._act(ctx, PhaseAction(
                PhaseActionKind.SUBMIT_STOP, phase.phase_id,
                side=phase.side, lots=self._lots(phase), price=price,
                reason='stop rest (to cancel)',
            ))

        if self._state == PhaseState.AWAIT_FILL:
            if ctx.filled_since_submit or ctx.open_position_count > 0:
                # A resting stop that fills is a mis-sided trigger, not bad luck: the
                # offset put it on the wrong side of the market and the venue took it.
                return self._finish(phase, PhaseOutcome.FAIL, 'filled before cancel', ctx.now)
            if ctx.unaccounted_since_submit:
                return self._finish_unaccounted(phase, ctx)
            if ctx.rejected_since_submit:
                return self._finish(phase, PhaseOutcome.FAIL, 'venue refused the stop', ctx.now)
            if ctx.active_stop_count >= 1:
                self._state = PhaseState.POST_CLOSE_WAIT
                return self._act(ctx, PhaseAction(
                    PhaseActionKind.CANCEL, phase.phase_id,
                    reason='cancel resting stop'))
            if self._timed_out(phase, ctx):
                return self._finish(phase, PhaseOutcome.FAIL, 'never rested', ctx.now)
            return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

        # POST_CLOSE_WAIT
        if ctx.active_stop_count == 0 and ctx.open_position_count == 0:
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
            if ctx.unaccounted_since_submit:
                return self._finish_unaccounted(phase, ctx)
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

    def _handle_close_and_end(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        """
        Open a position, then send its close and ask for the session's end in one pass (#630).

        The close is still on its way when the loop ends, so the stop has to wait for it before the
        session ends. Whether it did is read at that end (`conclude_at_session_end`); a pass that
        still comes after the close means the session did not end.

        Args:
            phase: The phase being driven
            ctx: This pass's observation

        Returns:
            The action to perform
        """
        if self._state == PhaseState.PENDING:
            if ctx.open_position_count > 0:
                return self._finish(phase, PhaseOutcome.FAIL, 'not flat at entry, nothing sent', ctx.now)
            self._state = PhaseState.AWAIT_FILL
            return self._act(ctx, PhaseAction(
                PhaseActionKind.SUBMIT_MARKET, phase.phase_id,
                side=phase.side, lots=self._lots(phase),
                reason='open, to close as the session ends',
            ))

        if self._state == PhaseState.AWAIT_FILL:
            if ctx.unaccounted_since_submit:
                return self._finish_unaccounted(phase, ctx)
            if ctx.rejected_since_submit:
                return self._finish(phase, PhaseOutcome.FAIL, 'open rejected', ctx.now)
            if ctx.filled_since_submit or ctx.open_position_count > 0:
                self._state = PhaseState.CLOSE
                return self._act(ctx, PhaseAction(
                    PhaseActionKind.CLOSE_ALL_AND_END_SESSION, phase.phase_id,
                    reason='close and end the session in one pass',
                ))
            if self._timed_out(phase, ctx):
                return self._finish(phase, PhaseOutcome.FAIL, 'open not filled', ctx.now)
            return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

        # CLOSE — the session's end concludes the phase
        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL, 'the session did not end', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_final(self, phase: FieldStudyPhase, ctx: PhaseContext) -> PhaseAction:
        return self._end_sequence(phase, ctx, 'field study complete')

    # ============================================
    # The session's end
    # ============================================

    def conclude_at_session_end(
        self,
        ctx: PhaseContext,
        reason: str,
        severity: SessionEndSeverity,
    ) -> None:
        """
        Conclude the phase whose close was on its way when the session ended (#630).

        Its close was sent in the pass that asked for the end, while its position was open. A
        close's fill reaches the study as a closed position, which it does not hear, so the book is
        the proof: flat only where the stop waited for the fill and booked it before this event.
        The phases after it were overtaken by that end and are concluded as skipped. Any other end
        concludes nothing, so a phase the run never reached stays missing.

        Args:
            ctx: The last observation, taken as the session ended
            reason: Why the session ended
            severity: How it ended
        """
        if self.is_complete():
            return
        phase = self._phases[self._idx]
        if phase.phase_type != PhaseType.CLOSE_AND_END or self._state != PhaseState.CLOSE:
            return
        problem = self._unsettled_close(ctx, reason, severity)
        if problem is None:
            self._finish(phase, PhaseOutcome.PASS,
                         'the stop waited for the close and booked its fill', ctx.now)
        else:
            self._finish(phase, PhaseOutcome.FAIL, problem, ctx.now)
        while not self.is_complete():
            self._phase_start = ctx.now
            self._finish(self._phases[self._idx], PhaseOutcome.SKIPPED,
                         f'the session ended in {phase.phase_id}', ctx.now)

    @staticmethod
    def _unsettled_close(
        ctx: PhaseContext,
        reason: str,
        severity: SessionEndSeverity,
    ) -> Optional[str]:
        """
        What the session's end shows of a close the stop did not settle.

        Args:
            ctx: The last observation, taken as the session ended
            reason: Why the session ended
            severity: How it ended

        Returns:
            Why the phase fails, or None when the close was booked before the end
        """
        if ctx.unaccounted_since_submit:
            problem = 'the close was given up unaccounted at the stop'
        elif ctx.rejected_since_submit:
            problem = 'the close was rejected'
        elif ctx.open_position_count > 0:
            problem = (f'the session ended before the close was booked: '
                       f'{ctx.open_position_count} position(s) open in the book')
        elif ctx.has_pending:
            problem = 'an order was still pending as the session ended'
        else:
            return None
        if severity is not SessionEndSeverity.NORMAL:
            problem += f' ({severity.value} end: {reason})'
        return problem
