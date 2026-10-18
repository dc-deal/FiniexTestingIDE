"""
FiniexTestingIDE - Security Study Phase Machine (#358)

Drives the security study's phases on the shared acceptance-study engine. Most of its orders are
meant to be refused, so a submit phase is judged by the outcome it names: a fill, a denial with
its reason, or a submit the circuit breaker suppressed. The reason a refusal names is the evidence
— `insufficient_funds` while the order guard lets an order through to its own checks,
`rejection_cooldown` once it holds the direction back — so a sequence of refusals that cost
nothing shows the cooldown arm, skip a direction, run out and reset.

Common flow per submit phase:
    PENDING → SUBMIT → AWAIT_FILL → the first outcome observed → PASS when it is the one expected

The hard stop ends the session itself, often on the pass right after the fill that tripped it —
before the buy was judged and before the phase that waits for the stop began. Both are concluded
when the session ends (`conclude_at_session_end`), from what was observed by then.
"""

from enum import StrEnum
from typing import Dict, List, Optional

from python.framework.acceptance_studies.abstract_study_phase_machine import (
    AbstractStudyPhaseMachine,
    PhaseHandler,
)
from python.framework.types.acceptance_study_types.security_cert_types import (
    ExpectedOutcome,
    SecurityCertPhase,
    SecurityPhaseType,
)
from python.framework.types.acceptance_study_types.study_phase_types import (
    PhaseAction,
    PhaseActionKind,
    PhaseContext,
    PhaseOutcome,
    PhaseResult,
    PhaseState,
)
from python.framework.types.decision_event_types import SessionEndSeverity
from python.framework.types.trading_env_types.order_types import RejectionReason


class SecurityCertPhaseMachine(AbstractStudyPhaseMachine):
    """
    Drives the security study's phase sequence from per-pass observations.

    Args:
        phases: Ordered phase list (disabled phases skip)
        flat_below_lots: A base holding under this counts as none — what is smaller than the
            venue's smallest order cannot be sold, and moves the account value by nothing worth
            measuring
        base_currency: The traded pair's base, as a refusal names it ('BTC')
    """

    def __init__(
        self,
        phases: List[SecurityCertPhase],
        flat_below_lots: float,
        base_currency: str,
    ):
        super().__init__(phases)
        self._flat_below_lots = flat_below_lots
        self._base_currency = base_currency

    def _build_handlers(self) -> Dict[StrEnum, PhaseHandler]:
        return {
            SecurityPhaseType.START_FLAT: self._handle_start_flat,
            SecurityPhaseType.SUBMIT_EXPECT: self._handle_submit_expect,
            SecurityPhaseType.WAIT: self._handle_wait,
            SecurityPhaseType.AWAIT_SESSION_END: self._handle_await_session_end,
            SecurityPhaseType.FORCE_CLOSE: self._handle_force_close,
            SecurityPhaseType.FINAL_SUMMARY: self._handle_final,
        }

    # ============================================
    # Phase handlers
    # ============================================

    def _handle_start_flat(self, phase: SecurityCertPhase, ctx: PhaseContext) -> PhaseAction:
        base = self._base_currency
        if ctx.base_holding is None:
            return self._abandon(phase, ctx, f'the {base} balance could not be read')
        if ctx.base_holding >= self._flat_below_lots:
            # The breaker values the whole account: a held base moves it with every tick, and
            # against limits this small that movement trips them at a moment nobody chose.
            return self._abandon(
                phase, ctx,
                f'the account holds {ctx.base_holding:.8f} {base} — the security study judges '
                f'the account value and needs no {base} in it: sell the {base}, or run the study '
                f'on a pair whose base the account does not hold')
        return self._finish(phase, PhaseOutcome.PASS, f'the account holds no {base}', ctx.now)

    def _handle_submit_expect(self, phase: SecurityCertPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.AWAIT_FILL
            return self._act(ctx, PhaseAction(
                PhaseActionKind.SUBMIT_MARKET, phase.phase_id, side=phase.side,
                reason=f'{phase.lot_rule.value}, expect {phase.expect.value}'))

        observed = self._judge_observed(phase, ctx)
        if observed is not None:
            return observed
        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL, 'no outcome within the timeout', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_wait(self, phase: SecurityCertPhase, ctx: PhaseContext) -> PhaseAction:
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.POST_FILL_WAIT
            self._submit_time = ctx.now
            return PhaseAction(PhaseActionKind.NONE, phase.phase_id, reason=f'wait {phase.wait_s:g} s')
        if (ctx.now - self._submit_time).total_seconds() >= phase.wait_s:
            return self._finish(phase, PhaseOutcome.PASS, f'waited {phase.wait_s:g} s', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_await_session_end(self, phase: SecurityCertPhase, ctx: PhaseContext) -> PhaseAction:
        # The hard stop ends the session while this phase waits, and the session's end concludes
        # it (`conclude_at_session_end`). A pass concludes it only when the stop did not come.
        if self._state == PhaseState.PENDING:
            self._state = PhaseState.POST_FILL_WAIT
            self._submit_time = ctx.now
            return PhaseAction(PhaseActionKind.NONE, phase.phase_id, reason='await the hard stop')
        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL,
                                'the session was not ended by the safety layer', ctx.now)
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _handle_force_close(self, phase: SecurityCertPhase, ctx: PhaseContext) -> PhaseAction:
        return self._close_out(phase, ctx, is_force=True)

    def _handle_final(self, phase: SecurityCertPhase, ctx: PhaseContext) -> PhaseAction:
        return self._end_sequence(phase, ctx, 'security study complete')

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
        Conclude the submit and the wait the hard stop overtook.

        The stop can end the session before the study sees another pass, so a session it ends
        leaves the buy unjudged and the phase that waits for it unentered. The
        buy is judged on what was observed by the end; the wait passes when the end was an
        EMERGENCY — what the safety layer's stop requests — and fails on any other end. The
        certificate holds the result against the breaker's own record besides.

        Args:
            ctx: The last observation, taken as the session ended
            reason: Why the session ended
            severity: How it ended
        """
        if self.is_complete():
            return
        phase = self._phases[self._idx]
        if phase.phase_type == SecurityPhaseType.SUBMIT_EXPECT and self._state == PhaseState.AWAIT_FILL:
            if self._judge_observed(phase, ctx) is None:
                self._finish(phase, PhaseOutcome.FAIL,
                             f'the session ended before an outcome: {reason}', ctx.now)
            if self.is_complete():
                return
            phase = self._phases[self._idx]
        if phase.phase_type != SecurityPhaseType.AWAIT_SESSION_END:
            return
        if self._phase_start is None:
            self._phase_start = ctx.now
        if severity == SessionEndSeverity.EMERGENCY:
            self._finish(phase, PhaseOutcome.PASS, f'ended by the safety layer: {reason}', ctx.now)
        else:
            self._finish(phase, PhaseOutcome.FAIL,
                         f'the session ended, but not by the safety layer: {reason}', ctx.now)

    # ============================================
    # Helpers
    # ============================================

    def _judge_observed(
        self,
        phase: SecurityCertPhase,
        ctx: PhaseContext,
    ) -> Optional[PhaseAction]:
        """
        Judge a sent order on the first outcome observed, if one was.

        Args:
            phase: The submit phase
            ctx: The observation

        Returns:
            The action that finishes the phase, or None while nothing was observed
        """
        if ctx.unaccounted_since_submit:
            return self._finish_unaccounted(phase, ctx)
        if ctx.suppressed_since_submit:
            return self._judge(phase, ExpectedOutcome.SUPPRESSED, None, ctx)
        if ctx.rejected_since_submit:
            return self._judge(phase, ExpectedOutcome.DENIED, ctx.refusal_reason, ctx)
        if ctx.filled_since_submit:
            return self._judge(phase, ExpectedOutcome.FILLED, None, ctx)
        return None

    def _judge(
        self,
        phase: SecurityCertPhase,
        observed: ExpectedOutcome,
        reason: Optional[RejectionReason],
        ctx: PhaseContext,
    ) -> PhaseAction:
        """
        Finish a submit phase on the outcome observed, against the one it names.

        Args:
            phase: The submit phase
            observed: What came of the order
            reason: The refusal's reason where it was refused
            ctx: The observation that carried it

        Returns:
            The action that finishes the phase
        """
        said = observed.value + (f' ({reason.value})' if reason is not None else '')
        if observed == phase.expect and (
                observed != ExpectedOutcome.DENIED or reason == phase.expect_reason):
            return self._finish(phase, PhaseOutcome.PASS, said, ctx.now)
        wanted = phase.expect.value + (
            f' ({phase.expect_reason.value})' if phase.expect_reason is not None else '')
        return self._finish(phase, PhaseOutcome.FAIL, f'{said}, expected {wanted}', ctx.now)

    def _abandon(self, phase: SecurityCertPhase, ctx: PhaseContext, reason: str) -> PhaseAction:
        """
        Fail the phase and end the stage: the phases after it would measure nothing.

        Args:
            phase: The phase that cannot pass
            ctx: The pass's observation
            reason: Why

        Returns:
            The END_SESSION action
        """
        self._finish(phase, PhaseOutcome.FAIL, reason, ctx.now)
        for rest in self._phases[self._idx:]:
            self._results.append(PhaseResult(
                phase_id=rest.phase_id,
                phase_type=rest.phase_type,
                outcome=PhaseOutcome.SKIPPED,
                reason=f'stage abandoned at {phase.phase_id}',
                started_utc=ctx.now,
                ended_utc=ctx.now,
            ))
        self._idx = len(self._phases)
        return PhaseAction(PhaseActionKind.END_SESSION, phase.phase_id,
                           reason=f'security study abandoned: {reason}')
