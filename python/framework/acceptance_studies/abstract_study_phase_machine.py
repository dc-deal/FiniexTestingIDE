"""
FiniexTestingIDE - Abstract Study Phase Machine (#332, #358)

The engine every acceptance study's phase sequence runs on. A pure state machine: it consumes a
PhaseContext observation per pass and emits a typed PhaseAction (submit / close / cancel / modify /
end-session / none). It performs no I/O and tracks no broker order ids — the decision logic owns
broker access, order-id bookkeeping, and event observation.

What is shared lives here: the sequence, a phase's entry, its wait clock, its time-out and its
result. What a phase DOES is the study's own — each concrete machine declares one handler per
phase type of its own set.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import Callable, Dict, List, Optional

from python.framework.types.acceptance_study_types.study_phase_types import (
    PhaseAction,
    PhaseActionKind,
    PhaseContext,
    PhaseOutcome,
    PhaseResult,
    PhaseState,
    StudyPhase,
)
from python.framework.types.decision_event_types import SessionEndSeverity

# One phase type's handler: the phase in progress and the pass's observation in, the action out
PhaseHandler = Callable[[StudyPhase, PhaseContext], PhaseAction]


class AbstractStudyPhaseMachine(ABC):
    """
    Drives a study's phase sequence from per-pass observations.

    Args:
        phases: Ordered phase list; a disabled phase is skipped
    """

    def __init__(self, phases: List[StudyPhase]):
        self._phases = phases

        self._idx = 0
        self._state = PhaseState.PENDING
        self._phase_start: Optional[datetime] = None
        self._submit_time: Optional[datetime] = None
        self._rearm_attempts = 0

        self._results: List[PhaseResult] = []

        self._handlers: Dict[StrEnum, PhaseHandler] = self._build_handlers()

    @abstractmethod
    def _build_handlers(self) -> Dict[StrEnum, PhaseHandler]:
        """
        One handler per phase type the study declares.

        Returns:
            Phase type → the method that runs a phase of that type
        """

    def _on_phase_entry(self, phase: StudyPhase) -> None:
        """
        Reset the study's own per-phase state when a phase begins — nothing by default.

        Args:
            phase: The phase that begins
        """

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
            self._rearm_attempts = 0
            self._on_phase_entry(phase)
            if not phase.enabled:
                return self._finish(phase, PhaseOutcome.SKIPPED, 'disabled', ctx.now)

        return self._handlers[phase.phase_type](phase, ctx)

    def conclude_at_session_end(
        self,
        ctx: PhaseContext,
        reason: str,
        severity: SessionEndSeverity,
    ) -> None:
        """
        Conclude what the session's end decides — nothing by default.

        A session can end while a phase still waits, and a study whose phases wait FOR that end
        concludes them here, from the last observation. A phase left open is not reached, and the
        record says exactly that.

        Args:
            ctx: The last observation, taken as the session ended
            reason: Why the session ended
            severity: How it ended
        """

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
        phase: StudyPhase,
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

    def _finish_unaccounted(self, phase: StudyPhase, ctx: PhaseContext) -> PhaseAction:
        """
        Fail the phase on an order the framework stopped asking about (#566).

        Such an order used to arrive as a rejection, and in a phase that expects one it passed as
        the refusal the phase was waiting for — while the venue may still hold it. Checked wherever
        a phase reads a rejection, before it; a phase that reads none (a close-out, the final
        summary) runs on, so the session still cleans up and still ends.

        Args:
            phase: The phase in progress
            ctx: The observation that carried it

        Returns:
            The action that finishes the phase
        """
        return self._finish(phase, PhaseOutcome.FAIL,
                            'order unaccounted — the venue may still hold it', ctx.now)

    def _timed_out(self, phase: StudyPhase, ctx: PhaseContext) -> bool:
        """Whether the current wait exceeded the phase fill timeout."""
        if self._submit_time is None:
            return False
        return (ctx.now - self._submit_time).total_seconds() > phase.fill_timeout_s

    # ============================================
    # Phases every study ends with
    # ============================================

    def _close_out(self, phase: StudyPhase, ctx: PhaseContext, is_force: bool) -> PhaseAction:
        """
        Close every open position; a force close also cancels every resting order.

        Args:
            phase: The phase in progress
            ctx: The pass's observation
            is_force: True for the safety net that clears resting orders as well

        Returns:
            The action of this pass
        """
        # The force close is the safety net, so it clears every resting order — a stop as well
        # as a limit. Counting limits alone let a resting stop pass as flat and stay armed at
        # the venue after the study (found 2026-10-14); the cancel already routes stops their way.
        resting = ctx.active_limit_count + (ctx.active_stop_count if is_force else 0)

        if self._state == PhaseState.PENDING:
            if ctx.open_position_count == 0 and resting == 0:
                return self._finish(phase, PhaseOutcome.SKIPPED, 'already flat', ctx.now)
            self._state = PhaseState.CLOSE
            if is_force and resting > 0:
                return self._act(ctx, PhaseAction(PhaseActionKind.CANCEL_ALL, phase.phase_id, reason='force cancel resting'))
            return self._act(ctx, PhaseAction(PhaseActionKind.CLOSE_ALL, phase.phase_id, reason='close all positions'))

        flat = ctx.open_position_count == 0 and (not is_force or resting == 0)
        if flat:
            return self._finish(phase, PhaseOutcome.PASS, 'flat', ctx.now)
        if self._timed_out(phase, ctx):
            return self._finish(phase, PhaseOutcome.FAIL, 'not flat within timeout', ctx.now)
        if not ctx.has_pending:
            if is_force and resting > 0:
                return self._act(ctx, PhaseAction(PhaseActionKind.CANCEL_ALL, phase.phase_id, reason='retry cancel'))
            if ctx.open_position_count > 0:
                return self._act(ctx, PhaseAction(PhaseActionKind.CLOSE_ALL, phase.phase_id, reason='retry close'))
        return PhaseAction(PhaseActionKind.NONE, phase.phase_id)

    def _end_sequence(self, phase: StudyPhase, ctx: PhaseContext, reason: str) -> PhaseAction:
        """
        Pass the final phase and ask for the session to end.

        Args:
            phase: The final phase
            ctx: The pass's observation
            reason: What the session-end request says

        Returns:
            The END_SESSION action
        """
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
        return PhaseAction(PhaseActionKind.END_SESSION, phase.phase_id, reason=reason)
