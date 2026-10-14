"""
FiniexTestingIDE - Security Study Phase Machine Tests (#358)

The security study's phases on the shared engine: a submit is judged by the outcome it names — a
fill, a denial with its reason, a submit the circuit breaker suppressed — and anything else fails
it with both outcomes in its reason. A stage whose account holds the base abandons itself before
it can cost anything, naming the currency and what to do; a wait lets time pass; a phase that
waits for the hard stop fails only when the stop never came — and since the stop ends the session
before the study sees another pass, the session's end concludes it, and the buy before it.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from python.framework.decision_logic.core.security_cert_study.security_cert_phase_machine import (
    SecurityCertPhaseMachine,
)
from python.framework.types.acceptance_study_types.security_cert_types import SecurityCertPhase
from python.framework.types.acceptance_study_types.study_phase_types import (
    PhaseActionKind,
    PhaseContext,
    PhaseOutcome,
    PhaseSide,
)
from python.framework.types.decision_event_types import SessionEndSeverity
from python.framework.types.trading_env_types.order_types import RejectionReason

_T0 = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
_MIN_LOT = 6e-05


def _ctx(secs: float, filled: bool = False, rejected: bool = False,
         reason: Optional[RejectionReason] = None, suppressed: bool = False,
         unaccounted: bool = False, base: Optional[float] = 0.0, open_pos: int = 0,
         pending: bool = False) -> PhaseContext:
    """A synthetic observation at T0 + secs."""
    return PhaseContext(
        now=_T0 + timedelta(seconds=secs), mid_price=100_000.0, open_position_count=open_pos,
        active_limit_count=0, active_stop_count=0, has_pending=pending,
        filled_since_submit=filled, rejected_since_submit=rejected,
        cancelled_since_submit=False, unaccounted_since_submit=unaccounted,
        current_position_lots=None, budget_ok=True, refusal_reason=reason,
        suppressed_since_submit=suppressed, base_holding=base)


def _phase(phase_id: str, phase_type: str, **kw) -> SecurityCertPhase:
    return SecurityCertPhase.from_dict({'phase_id': phase_id, 'phase_type': phase_type, **kw})


def _submit(expect: str, reason: Optional[str] = None, **kw) -> SecurityCertPhase:
    spec = {'side': 'long', 'expect': expect, **kw}
    if reason:
        spec['expect_reason'] = reason
    return _phase('p', 'submit_expect', **spec)


def _machine(*phases: SecurityCertPhase) -> SecurityCertPhaseMachine:
    return SecurityCertPhaseMachine(list(phases), flat_below_lots=_MIN_LOT, base_currency='BTC')


def _judged(phase: SecurityCertPhase, outcome_ctx: PhaseContext):
    """Run one submit phase: the submit, then the observation that answers it."""
    machine = _machine(phase)
    assert machine.advance(_ctx(0)).kind == PhaseActionKind.SUBMIT_MARKET
    machine.advance(outcome_ctx)
    return machine.get_results()[0]


class TestTheAccountMustHoldNoBase:

    def test_an_account_without_the_base_passes(self):
        machine = _machine(_phase('flat', 'start_flat'))
        machine.advance(_ctx(0, base=0.0))
        result = machine.get_results()[0]
        assert (result.outcome, result.reason) == (PhaseOutcome.PASS, 'the account holds no BTC')

    def test_a_held_base_abandons_the_stage_and_says_what_to_do(self):
        machine = _machine(_phase('flat', 'start_flat'), _submit('filled'))
        action = machine.advance(_ctx(0, base=0.0002))
        first, rest = machine.get_results()
        assert action.kind == PhaseActionKind.END_SESSION
        assert first.outcome == PhaseOutcome.FAIL
        assert '0.00020000 BTC' in first.reason and 'sell the BTC' in first.reason
        assert rest.outcome == PhaseOutcome.SKIPPED, 'nothing after it may cost money'
        assert machine.is_complete()

    def test_dust_below_the_smallest_order_counts_as_none(self):
        machine = _machine(_phase('flat', 'start_flat'))
        machine.advance(_ctx(0, base=_MIN_LOT / 3))
        assert machine.get_results()[0].outcome == PhaseOutcome.PASS

    def test_an_unreadable_balance_abandons_the_stage(self):
        machine = _machine(_phase('flat', 'start_flat'))
        machine.advance(_ctx(0, base=None))
        assert machine.get_results()[0].outcome == PhaseOutcome.FAIL


class TestASubmitIsJudgedByTheOutcomeItNames:

    def test_the_submit_goes_out_on_the_phase_s_side(self):
        machine = _machine(_submit('filled', side='short'))
        action = machine.advance(_ctx(0))
        assert (action.kind, action.side) == (PhaseActionKind.SUBMIT_MARKET, PhaseSide.SHORT)

    def test_an_expected_fill_passes(self):
        assert _judged(_submit('filled'), _ctx(1, filled=True)).outcome == PhaseOutcome.PASS

    def test_an_expected_denial_with_its_reason_passes(self):
        result = _judged(_submit('denied', 'rejection_cooldown'),
                         _ctx(1, rejected=True, reason=RejectionReason.REJECTION_COOLDOWN))
        assert (result.outcome, result.reason) == (
            PhaseOutcome.PASS, 'denied (rejection_cooldown)')

    def test_a_denial_for_another_reason_fails_and_names_both(self):
        result = _judged(_submit('denied', 'rejection_cooldown'),
                         _ctx(1, rejected=True, reason=RejectionReason.INSUFFICIENT_FUNDS))
        assert result.outcome == PhaseOutcome.FAIL
        assert result.reason == ('denied (insufficient_funds), expected denied '
                                 '(rejection_cooldown)')

    def test_an_expected_suppression_passes(self):
        assert _judged(_submit('suppressed'), _ctx(1, suppressed=True)).outcome == (
            PhaseOutcome.PASS)

    def test_a_fill_where_a_suppression_was_expected_fails(self):
        result = _judged(_submit('suppressed'), _ctx(1, filled=True))
        assert (result.outcome, result.reason) == (
            PhaseOutcome.FAIL, 'filled, expected suppressed')

    def test_an_unaccounted_order_fails(self):
        result = _judged(_submit('denied', 'insufficient_funds'),
                         _ctx(1, unaccounted=True, rejected=True,
                              reason=RejectionReason.INSUFFICIENT_FUNDS))
        assert result.outcome == PhaseOutcome.FAIL

    def test_no_outcome_within_the_timeout_fails(self):
        result = _judged(_submit('filled', fill_timeout_s=30), _ctx(31))
        assert (result.outcome, result.reason) == (
            PhaseOutcome.FAIL, 'no outcome within the timeout')


class TestTimePasses:

    def test_a_wait_passes_once_its_time_is_up(self):
        machine = _machine(_phase('w', 'wait', wait_s=16))
        machine.advance(_ctx(0))
        machine.advance(_ctx(10))
        assert machine.get_results() == []
        machine.advance(_ctx(16))
        assert machine.get_results()[0].outcome == PhaseOutcome.PASS

    def test_a_hard_stop_that_never_came_fails_its_wait(self):
        machine = _machine(_phase('stop', 'await_session_end', fill_timeout_s=60))
        machine.advance(_ctx(0))
        machine.advance(_ctx(30))
        assert machine.get_results() == [], 'still waiting — the stop may end the session'
        machine.advance(_ctx(61))
        result = machine.get_results()[0]
        assert (result.outcome, result.reason) == (
            PhaseOutcome.FAIL, 'the session was not ended by the safety layer')


class TestTheStageEnds:

    def test_the_force_close_closes_what_is_open(self):
        machine = _machine(_phase('close', 'force_close'))
        assert machine.advance(_ctx(0, open_pos=1)).kind == PhaseActionKind.CLOSE_ALL
        machine.advance(_ctx(1, open_pos=0))
        assert machine.get_results()[0].outcome == PhaseOutcome.PASS

    def test_the_final_phase_asks_for_the_session_to_end(self):
        machine = _machine(_phase('final', 'final_summary'))
        assert machine.advance(_ctx(0)).kind == PhaseActionKind.END_SESSION
        assert machine.get_results()[0].outcome == PhaseOutcome.PASS


class TestTheSessionsEndConcludesWhatTheStopOvertook:
    """
    The hard stop ends the session before the study sees another pass: the buy that tripped it
    is unjudged, the phase that waits for it unentered. The session's end concludes both.
    """

    _HOLDINGS_KEPT = 'emergency stop, spot holdings kept (hard drawdown 0.04 > 0.00)'

    @staticmethod
    def _stage():
        """A hard-stop stage after its buy was sent and before it was judged."""
        machine = _machine(_submit('filled'),
                           _phase('stop', 'await_session_end', fill_timeout_s=60))
        machine.advance(_ctx(0))
        return machine

    def test_an_emergency_end_judges_the_buy_and_passes_the_wait(self):
        machine = self._stage()

        machine.conclude_at_session_end(_ctx(1, filled=True), self._HOLDINGS_KEPT,
                                        SessionEndSeverity.EMERGENCY)

        buy, stop = machine.get_results()
        assert (buy.outcome, buy.reason) == (PhaseOutcome.PASS, 'filled')
        assert (stop.outcome, stop.reason) == (
            PhaseOutcome.PASS, f'ended by the safety layer: {self._HOLDINGS_KEPT}')
        assert machine.is_complete()

    def test_any_other_end_fails_the_wait(self):
        machine = self._stage()

        machine.conclude_at_session_end(_ctx(1, filled=True), 'tick source exhausted',
                                        SessionEndSeverity.NORMAL)

        stop = machine.get_results()[-1]
        assert (stop.outcome, stop.reason) == (
            PhaseOutcome.FAIL, 'the session ended, but not by the safety layer: '
                               'tick source exhausted')

    def test_a_buy_with_no_outcome_by_the_end_fails(self):
        machine = self._stage()

        machine.conclude_at_session_end(_ctx(1), self._HOLDINGS_KEPT, SessionEndSeverity.EMERGENCY)

        buy = machine.get_results()[0]
        assert buy.outcome == PhaseOutcome.FAIL
        assert buy.reason.startswith('the session ended before an outcome')

    def test_a_wait_already_in_progress_is_concluded_too(self):
        machine = _machine(_phase('stop', 'await_session_end', fill_timeout_s=60))
        machine.advance(_ctx(0))
        machine.advance(_ctx(5))

        machine.conclude_at_session_end(_ctx(6), self._HOLDINGS_KEPT, SessionEndSeverity.EMERGENCY)

        assert machine.get_results()[0].outcome == PhaseOutcome.PASS

    def test_a_finished_sequence_concludes_nothing(self):
        machine = _machine(_phase('final', 'final_summary'))
        machine.advance(_ctx(0))

        machine.conclude_at_session_end(_ctx(1), 'final summary', SessionEndSeverity.NORMAL)

        assert [r.phase_id for r in machine.get_results()] == ['final']

    def test_a_phase_that_waits_for_nothing_is_left_unreached(self):
        # A session that ends during a wait proves nothing about the wait; the record says it
        # was not reached rather than inventing a verdict.
        machine = _machine(_phase('w', 'wait', wait_s=16))
        machine.advance(_ctx(0))

        machine.conclude_at_session_end(_ctx(1), 'operator stop', SessionEndSeverity.NORMAL)

        assert machine.get_results() == []
