"""
Loop Cadence — Phase Machine Sensitivity to Observation Cadence (#360)

The FieldStudyPhaseMachine is a pure state machine driven by per-pass observations
(PhaseContext.now + counts). #360 changes WHEN those observations happen: a ghost-pass
runs ~1/s with a continuously-advancing clock, so a cancel/fill resolution that lands
between real ticks is observed promptly — instead of only at the next real tick, by
which time the wall-clock budget has already burned through a long tick gap.

These tests pin that cause→effect at the machine level (no broker, fully deterministic):
the same cancel phase PASSES when the resolution is observed on a ghost-pass within the
budget, and FAILS when it is only checkable at a far-future tick (the pre-#360 behavior
that produced the field-study `multi_cancel_all → cancel-all not confirmed` blocker).
"""

from datetime import datetime, timedelta, timezone

from python.framework.decision_logic.core.live_field_study.field_study_phase_machine import (
    FieldStudyPhaseMachine,
)
from python.framework.types.autotrader_types.field_study_types import (
    FieldStudyPhase,
    PhaseContext,
    PhaseOutcome,
    PhaseType,
)

_T0 = datetime(2026, 6, 3, 18, 0, 0, tzinfo=timezone.utc)


def _machine(fill_timeout_s: float = 30.0) -> FieldStudyPhaseMachine:
    phase = FieldStudyPhase(
        phase_id='multi_cancel_all',
        phase_type=PhaseType.MULTI_CANCEL,
        fill_timeout_s=fill_timeout_s,
    )
    return FieldStudyPhaseMachine(
        phases=[phase],
        default_lot=0.01,
        default_limit_offset_pct=0.002,
        max_rearm_attempts=0,
        rearm_shrink_pct=0.25,
    )


def _ctx(elapsed_s: float, active_limit_count: int) -> PhaseContext:
    return PhaseContext(
        now=_T0 + timedelta(seconds=elapsed_s),
        mid_price=50000.0,
        open_position_count=0,
        active_limit_count=active_limit_count,
        has_pending=False,
        filled_since_submit=False,
        rejected_since_submit=False,
        cancelled_since_submit=False,
        current_position_lots=None,
        budget_ok=True,
    )


class TestCancelObservationCadence:
    """The cancel phase outcome depends on when the resolution is observed."""

    def test_passes_when_resolution_observed_on_ghost_pass(self):
        """
        Ghost cadence (#360): observations ~1/s with an advancing clock. The cancel
        resolves at ~3 s and is observed there → PASS, well inside the 30 s budget.
        """
        m = _machine(fill_timeout_s=30.0)
        m.advance(_ctx(0.0, active_limit_count=2))   # PENDING → CANCEL_ALL issued
        m.advance(_ctx(1.0, active_limit_count=2))   # still resting
        m.advance(_ctx(2.0, active_limit_count=2))   # still resting
        m.advance(_ctx(3.0, active_limit_count=0))   # resolution observed → PASS

        assert m.is_complete()
        results = m.get_results()
        assert len(results) == 1
        assert results[0].outcome == PhaseOutcome.PASS

    def test_fails_when_resolution_only_visible_at_far_tick(self):
        """
        Pre-#360 (tick-only): the next observation is one big tick gap away (35 s),
        and the resolution was not pulled during the gap (re-poll was tick-gated),
        so the count is still > 0 and the budget has burned → FAIL. This is the
        field-study `cancel-all not confirmed` blocker.
        """
        m = _machine(fill_timeout_s=30.0)
        m.advance(_ctx(0.0, active_limit_count=2))    # PENDING → CANCEL_ALL issued
        m.advance(_ctx(35.0, active_limit_count=2))   # next tick, still resting → timeout

        assert m.is_complete()
        results = m.get_results()
        assert len(results) == 1
        assert results[0].outcome == PhaseOutcome.FAIL
        assert 'not confirmed' in results[0].reason
