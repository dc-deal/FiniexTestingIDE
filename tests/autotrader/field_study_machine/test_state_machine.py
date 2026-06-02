"""
FiniexTestingIDE - Field Study Phase Machine Tests (#332)

Deterministic tests for FieldStudyPhaseMachine driven by synthetic PhaseContext
observations — no broker, no network. Covers the per-phase flows, the re-arm
bound, expected-rejection handling, and the skip/end paths.
"""

from datetime import datetime, timedelta, timezone

from python.framework.decision_logic.core.live_field_study.field_study_phase_machine import (
    FieldStudyPhaseMachine,
)
from python.framework.types.autotrader_types.field_study_types import (
    FieldStudyPhase,
    PhaseActionKind,
    PhaseContext,
    PhaseOutcome,
)

_T0 = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(secs, open_pos=0, limits=0, pending=False, filled=False,
         rejected=False, lots=None, budget_ok=True):
    """Build a synthetic per-tick observation at T0 + secs."""
    return PhaseContext(
        now=_T0 + timedelta(seconds=secs),
        mid_price=2000.0,
        open_position_count=open_pos,
        active_limit_count=limits,
        has_pending=pending,
        filled_since_submit=filled,
        rejected_since_submit=rejected,
        cancelled_since_submit=False,
        current_position_lots=lots,
        budget_ok=budget_ok,
    )


def _phase(phase_id, phase_type, **kw):
    return FieldStudyPhase.from_dict({'phase_id': phase_id, 'phase_type': phase_type, **kw})


def _machine(phases, max_rearm=3):
    return FieldStudyPhaseMachine(
        phases=phases,
        default_lot=0.01,
        default_limit_offset_pct=0.002,
        max_rearm_attempts=max_rearm,
        rearm_shrink_pct=0.25,
    )


def test_market_open_fills_pass():
    m = _machine([_phase('p', 'market_open', side='long')])
    assert m.advance(_ctx(1)).kind == PhaseActionKind.SUBMIT_MARKET
    m.advance(_ctx(2))
    m.advance(_ctx(3, open_pos=1, filled=True))
    assert m.is_complete()
    assert m.get_results()[0].outcome == PhaseOutcome.PASS


def test_expected_rejection_passes():
    m = _machine([_phase('p', 'market_open', side='short', expect_rejection=True)])
    m.advance(_ctx(1))
    m.advance(_ctx(2, rejected=True))
    assert m.get_results()[0].outcome == PhaseOutcome.EXPECTED_REJECTION


def test_strict_rejection_fails_on_fill():
    m = _machine([_phase('p', 'market_open', side='long', expect_rejection=True)])
    m.advance(_ctx(1))
    m.advance(_ctx(2, open_pos=1, filled=True))
    assert m.get_results()[0].outcome == PhaseOutcome.FAIL


def test_market_open_timeout_fails():
    m = _machine([_phase('p', 'market_open', side='long', fill_timeout_s=5)])
    m.advance(_ctx(1))
    m.advance(_ctx(20))  # no fill, past timeout
    assert m.get_results()[0].outcome == PhaseOutcome.FAIL


def test_limit_rearm_exhaustion_inconclusive():
    # Budget exhausted without a fill is market-dependent (the limit was never
    # reached), not a mechanical failure → INCONCLUSIVE, not FAIL.
    m = _machine([_phase('p', 'limit_open', side='long', rearm=True, fill_timeout_s=5)], max_rearm=2)
    assert m.advance(_ctx(1)).kind == PhaseActionKind.SUBMIT_LIMIT
    assert m.advance(_ctx(10, limits=1)).kind == PhaseActionKind.CANCEL_ALL   # rearm 1
    assert m.advance(_ctx(11, limits=0)).kind == PhaseActionKind.SUBMIT_LIMIT
    assert m.advance(_ctx(20, limits=1)).kind == PhaseActionKind.CANCEL_ALL   # rearm 2
    assert m.advance(_ctx(21, limits=0)).kind == PhaseActionKind.SUBMIT_LIMIT
    m.advance(_ctx(30, limits=1))  # timeout, rearm budget exhausted
    result = m.get_results()[0]
    assert result.outcome == PhaseOutcome.INCONCLUSIVE
    assert result.rearm_attempts == 2


def test_limit_rearm_fill_during_cancel_passes_no_orphan():
    # Cancel-vs-fill race during a re-arm: the "stale" limit filled instead of
    # cancelling. On the next SUBMIT tick a position exists → PASS, and the phase
    # must NOT submit another order (which would orphan + block later phases).
    m = _machine([_phase('p', 'limit_open', side='long', rearm=True, fill_timeout_s=5)])
    assert m.advance(_ctx(1)).kind == PhaseActionKind.SUBMIT_LIMIT
    assert m.advance(_ctx(10, limits=1)).kind == PhaseActionKind.CANCEL_ALL   # re-arm
    action = m.advance(_ctx(11, open_pos=1, filled=True))   # the "cancelled" limit filled
    assert action.kind != PhaseActionKind.SUBMIT_LIMIT      # no orphan submitted
    assert m.get_results()[0].outcome == PhaseOutcome.PASS


def test_limit_modify_no_fill_inconclusive():
    # Modified toward market but never filled → market-dependent → INCONCLUSIVE.
    m = _machine([_phase('p', 'limit_modify', side='long', fill_timeout_s=5)])
    assert m.advance(_ctx(1)).kind == PhaseActionKind.SUBMIT_LIMIT
    assert m.advance(_ctx(2, limits=1)).kind == PhaseActionKind.MODIFY_LIMIT   # step 0→1
    m.advance(_ctx(20, limits=1))  # timeout after modify, no fill
    assert m.get_results()[0].outcome == PhaseOutcome.INCONCLUSIVE


def test_limit_modify_never_rested_fails():
    # The limit never rested at the broker → mechanical failure → FAIL.
    m = _machine([_phase('p', 'limit_modify', side='long', fill_timeout_s=5)])
    assert m.advance(_ctx(1)).kind == PhaseActionKind.SUBMIT_LIMIT
    m.advance(_ctx(20))  # never rested (limits stays 0), timeout at step 0
    assert m.get_results()[0].outcome == PhaseOutcome.FAIL


def test_limit_open_fills_pass():
    m = _machine([_phase('p', 'limit_open', side='long', rearm=True)])
    assert m.advance(_ctx(1)).kind == PhaseActionKind.SUBMIT_LIMIT
    m.advance(_ctx(2, limits=1))            # resting
    m.advance(_ctx(3, open_pos=1, filled=True))  # filled
    assert m.get_results()[0].outcome == PhaseOutcome.PASS


def test_multi_limit_all_watching_pass():
    m = _machine([_phase('p', 'multi_limit', side='long', order_count=2)])
    assert m.advance(_ctx(1)).kind == PhaseActionKind.SUBMIT_LIMIT
    assert m.advance(_ctx(2)).kind == PhaseActionKind.SUBMIT_LIMIT
    m.advance(_ctx(3, limits=2))
    assert m.get_results()[0].outcome == PhaseOutcome.PASS


def test_partial_close_half_then_flat_pass():
    m = _machine([_phase('p', 'partial_close', side='long')])
    assert m.advance(_ctx(1)).kind == PhaseActionKind.SUBMIT_MARKET
    action = m.advance(_ctx(2, open_pos=1, filled=True, lots=0.02))
    assert action.kind == PhaseActionKind.CLOSE_PARTIAL and action.close_fraction == 0.5
    assert m.advance(_ctx(3, open_pos=1, lots=0.01)).kind == PhaseActionKind.CLOSE_ALL
    m.advance(_ctx(4, open_pos=0))
    assert m.get_results()[0].outcome == PhaseOutcome.PASS


def test_idle_completes_after_duration():
    m = _machine([_phase('p', 'idle', idle_seconds=10)])
    assert m.advance(_ctx(1)).kind == PhaseActionKind.NONE
    assert m.advance(_ctx(5)).kind == PhaseActionKind.NONE
    m.advance(_ctx(12))
    assert m.get_results()[0].outcome == PhaseOutcome.PASS


def test_force_close_already_flat_skipped():
    m = _machine([_phase('p', 'force_close')])
    m.advance(_ctx(1, open_pos=0, limits=0))
    assert m.get_results()[0].outcome == PhaseOutcome.SKIPPED


def test_close_all_closes_then_flat():
    m = _machine([_phase('p', 'market_close_all')])
    assert m.advance(_ctx(1, open_pos=1)).kind == PhaseActionKind.CLOSE_ALL
    m.advance(_ctx(2, open_pos=0))
    assert m.get_results()[0].outcome == PhaseOutcome.PASS


def test_disabled_phase_skipped():
    m = _machine([_phase('p', 'market_open', side='long', enabled=False)])
    m.advance(_ctx(1))
    assert m.get_results()[0].outcome == PhaseOutcome.SKIPPED


def test_final_summary_ends_session():
    m = _machine([_phase('p', 'final_summary')])
    assert m.advance(_ctx(1)).kind == PhaseActionKind.END_SESSION
    assert m.is_complete()
    assert m.get_results()[0].outcome == PhaseOutcome.PASS


def test_full_sequence_runs_to_completion():
    phases = [
        _phase('open', 'market_open', side='long'),
        _phase('close', 'market_close_all'),
        _phase('final', 'final_summary'),
    ]
    m = _machine(phases)
    m.advance(_ctx(1))                          # submit
    m.advance(_ctx(3, open_pos=1, filled=True))  # fill open -> PASS
    m.advance(_ctx(4, open_pos=1))               # close phase -> CLOSE_ALL
    m.advance(_ctx(5, open_pos=0))               # flat -> PASS
    end = m.advance(_ctx(6))                      # final -> END_SESSION
    assert end.kind == PhaseActionKind.END_SESSION
    assert m.is_complete()
    assert [r.outcome for r in m.get_results()] == [
        PhaseOutcome.PASS, PhaseOutcome.PASS, PhaseOutcome.PASS
    ]
