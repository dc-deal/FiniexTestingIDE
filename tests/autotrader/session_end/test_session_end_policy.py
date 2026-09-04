"""
FiniexTestingIDE - Session-End Policy Tests (#492)

The policy layer: what a profile may declare, what it may not, and which combinations are
refused before the session starts. Drives `resolve_session_end_policy` directly — the
loader and the executor have their own suites, and a refusal here has to be provable
without running a session.

Three things are asserted that a passing session would hide:
- `positions: 'close'` refuses instead of quietly behaving like 'leave'
- a profile may TIGHTEN the orders axis freely and may not loosen it
- `orders: 'leave'` with an adoption mode that cannot confirm is refused as a PAIR — and
  is NOT refused when the algo accounts for its inherited orders itself (#493)
"""

import pytest

from python.framework.exceptions.live_execution_errors import (
    SessionEndCloseUnsupportedError,
    SessionEndPolicyConflictError,
)
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.config_types.autotrader_defaults_config_types import (
    ColdStartDefaults,
    SessionEndDefaults,
)
from python.framework.validators.session_end_validator import resolve_session_end_policy


class _NoHook:
    """A decision logic that does not answer for the boot situation."""


class _WithColdStartHook:
    """A decision logic that accounts for its inherited orders itself (#493)."""

    def on_cold_start(self, situation):
        """Args: situation: The boot situation. Returns: Nothing — a stub."""
        return None


def _config(
    orders: str = 'cancel',
    positions: str = 'leave',
    adoption_mode: str = 'operator_confirm',
    cold_start_enabled: bool = True,
) -> AutoTraderConfig:
    """
    Build a profile carrying just the two blocks the policy is resolved from.

    Args:
        orders: The profile's session_end.orders
        positions: The profile's session_end.positions
        adoption_mode: The profile's cold_start.adoption_mode
        cold_start_enabled: Whether cold start is on at all

    Returns:
        The configuration
    """
    return AutoTraderConfig(
        name='session_end_probe',
        symbol='BTCUSD',
        broker_type='kraken_spot',
        session_end=SessionEndDefaults(orders=orders, positions=positions),
        cold_start=ColdStartDefaults(
            enabled=cold_start_enabled, adoption_mode=adoption_mode),
    )


class TestDefaults:
    """The shipped default is the market norm: cancel the orders, leave the position."""

    def test_the_default_policy_resolves_unchanged(self):
        policy = resolve_session_end_policy(
            _config(), broker_posture='cancel', decision_logic=_NoHook(), attended=False)

        assert policy.orders == 'cancel'
        assert policy.positions == 'leave'

    def test_the_two_axes_are_independent(self):
        """Leaving orders standing says nothing about the position, and the reverse."""
        policy = resolve_session_end_policy(
            _config(orders='leave', adoption_mode='auto'),
            broker_posture='leave', decision_logic=_NoHook(), attended=False)

        assert policy.orders == 'leave'
        assert policy.positions == 'leave'


class TestPositionsCloseIsDeclaredNotBuilt:
    """`close` refuses rather than silently behaving like `leave`."""

    def test_it_refuses_and_names_the_issue_that_unblocks_it(self):
        with pytest.raises(SessionEndCloseUnsupportedError) as exc:
            resolve_session_end_policy(
                _config(positions='close'), broker_posture='cancel',
                decision_logic=_NoHook(), attended=False)

        message = str(exc.value)
        assert '#487' in message, 'the refusal must name what unblocks it'
        assert 'session_end.positions' in message

    def test_it_does_not_fall_back_to_leave(self):
        """A silent fallback would leave a profile claiming something it does not do."""
        with pytest.raises(SessionEndCloseUnsupportedError):
            resolve_session_end_policy(
                _config(positions='close', adoption_mode='auto'),
                broker_posture='cancel', decision_logic=_NoHook(), attended=False)


class TestLooseningAsymmetry:
    """A profile may tighten the orders axis; loosening needs the broker's posture."""

    def test_a_profile_may_tighten_against_a_leave_broker(self):
        policy = resolve_session_end_policy(
            _config(orders='cancel'), broker_posture='leave',
            decision_logic=_NoHook(), attended=False)

        assert policy.orders == 'cancel'

    def test_a_profile_may_not_loosen_against_a_cancel_broker(self):
        with pytest.raises(SessionEndPolicyConflictError) as exc:
            resolve_session_end_policy(
                _config(orders='leave', adoption_mode='auto'),
                broker_posture='cancel', decision_logic=_NoHook(), attended=False)

        message = str(exc.value)
        # Both sides named, or the operator cannot tell which file to change.
        assert 'session_end.orders' in message
        assert 'market_config.json' in message
        assert 'kraken_spot' in message

    def test_the_attempt_is_refused_rather_than_ignored(self):
        """
        Silently ignoring it would leave a profile that reads as one thing and does another.

        This is the `dry_run` near-miss (#304) in the other axis: a declared, documented,
        parsed field that nothing acts on.
        """
        with pytest.raises(SessionEndPolicyConflictError):
            resolve_session_end_policy(
                _config(orders='leave', adoption_mode='auto'),
                broker_posture='cancel', decision_logic=_NoHook(), attended=False)


class TestTheIncoherentPair:
    """
    `orders: 'leave'` is only useful if the next boot may adopt them back.

    The pair fires at 03:14 and is invisible while writing the config, which is why it is
    checked at startup rather than discovered there.
    """

    def test_leave_plus_operator_confirm_unattended_is_refused(self):
        with pytest.raises(SessionEndPolicyConflictError) as exc:
            resolve_session_end_policy(
                _config(orders='leave', adoption_mode='operator_confirm'),
                broker_posture='leave', decision_logic=_NoHook(), attended=False)

        message = str(exc.value)
        assert 'session_end.orders' in message
        assert 'cold_start.adoption_mode' in message
        # And it says how to get out of it, all three ways.
        assert '--attended' in message
        assert "'auto'" in message
        assert 'on_cold_start' in message

    def test_it_is_allowed_when_an_operator_is_declared_present(self):
        policy = resolve_session_end_policy(
            _config(orders='leave', adoption_mode='operator_confirm'),
            broker_posture='leave', decision_logic=_NoHook(), attended=True)

        assert policy.orders == 'leave'

    def test_it_is_allowed_when_the_algo_accounts_for_the_orders(self):
        """A correctly built bot must NOT be caught by this check (#493)."""
        policy = resolve_session_end_policy(
            _config(orders='leave', adoption_mode='operator_confirm'),
            broker_posture='leave', decision_logic=_WithColdStartHook(), attended=False)

        assert policy.orders == 'leave'

    def test_it_is_allowed_with_automatic_adoption(self):
        policy = resolve_session_end_policy(
            _config(orders='leave', adoption_mode='auto'),
            broker_posture='leave', decision_logic=_NoHook(), attended=False)

        assert policy.orders == 'leave'

    def test_leave_with_cold_start_disabled_is_refused(self):
        """Nothing would ever adopt them back — the orders would simply be abandoned."""
        with pytest.raises(SessionEndPolicyConflictError) as exc:
            resolve_session_end_policy(
                _config(orders='leave', cold_start_enabled=False),
                broker_posture='leave', decision_logic=_WithColdStartHook(), attended=True)

        assert 'cold_start.enabled' in str(exc.value)

    def test_cancel_never_triggers_the_pair_check(self):
        """The pair only exists because the orders survive; cancelling ends the question."""
        policy = resolve_session_end_policy(
            _config(orders='cancel', adoption_mode='operator_confirm'),
            broker_posture='cancel', decision_logic=_NoHook(), attended=False)

        assert policy.orders == 'cancel'
