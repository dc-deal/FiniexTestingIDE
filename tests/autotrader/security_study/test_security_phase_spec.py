"""
FiniexTestingIDE - Security Study Phase Declarations (#358)

A phase is read from its profile, and the declaration that would certify nothing is refused when
the profile loads, not discovered on a real account: a submit that names no expected outcome, a
denial that names no reason — it could not tell an invalid lot from a cooldown — a reason on an
outcome that is not a denial, and a submit with no side.
"""

import pytest

from python.framework.types.acceptance_study_types.security_cert_types import (
    ExpectedOutcome,
    LotRule,
    SecurityCertPhase,
    SecurityPhaseType,
)
from python.framework.types.acceptance_study_types.study_phase_types import PhaseSide
from python.framework.types.trading_env_types.order_types import RejectionReason


def test_a_submit_is_read_with_everything_it_declares():
    phase = SecurityCertPhase.from_dict({
        'phase_id': 'g_cooled_buy', 'phase_type': 'submit_expect', 'side': 'long',
        'lot_rule': 'min_lot', 'expect': 'denied', 'expect_reason': 'rejection_cooldown',
        'fill_timeout_s': 20, '_comment': 'ignored'})
    assert (phase.phase_type, phase.side, phase.lot_rule, phase.expect, phase.expect_reason,
            phase.fill_timeout_s) == (
        SecurityPhaseType.SUBMIT_EXPECT, PhaseSide.LONG, LotRule.MIN_LOT, ExpectedOutcome.DENIED,
        RejectionReason.REJECTION_COOLDOWN, 20.0)


@pytest.mark.parametrize('spec, refusal', [
    ({'side': 'long'}, 'names `expect`'),
    ({'side': 'long', 'expect': 'denied'}, '`expect_reason`'),
    ({'side': 'long', 'expect': 'filled', 'expect_reason': 'insufficient_funds'},
     '`expect_reason`'),
    ({'expect': 'filled'}, 'names its `side`'),
], ids=['no_expectation', 'denial_without_reason', 'reason_without_denial', 'no_side'])
def test_a_submit_that_would_certify_nothing_is_refused(spec, refusal):
    with pytest.raises(ValueError, match=refusal):
        SecurityCertPhase.from_dict({'phase_id': 'p', 'phase_type': 'submit_expect', **spec})


def test_an_unknown_reason_is_refused_by_its_enum():
    with pytest.raises(ValueError):
        SecurityCertPhase.from_dict({'phase_id': 'p', 'phase_type': 'submit_expect',
                                     'side': 'long', 'expect': 'denied',
                                     'expect_reason': 'rejection_cooldwn'})
