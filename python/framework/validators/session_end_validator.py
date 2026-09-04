"""
FiniexTestingIDE - Session-End Policy Validation (#492)

Resolves what a live session does with what it still holds when it ends, and refuses the
combinations that would leave the operator worse off than either half suggests. One thin
call from `AutotraderMain.run()`, before anything is set up — a refusal here must arrive
before the first order, not after.

Three questions, in the order they can be answered:

1. May this profile LOOSEN the orders axis? `'leave'` needs the broker's standing posture
   behind it (market_config.json::session_end_orders), the same asymmetry `dry_run`
   carries — a profile may always tighten.
2. Is `positions: 'close'` being asked for? Declared, not built — see the exception.
3. Is the pair with cold-start adoption coherent? Leaving orders behind only helps if the
   next boot may adopt them, and that is the check worth more than the setting itself.

The dry-run resolution still lives in `autotrader_main._is_dry_run()`. This one lives in a
validator because the third question needs the decision logic, and because the reasoning is
longer than a call site should carry (§14).
"""

from typing import Any

from python.framework.exceptions.live_execution_errors import (
    SessionEndCloseUnsupportedError,
    SessionEndPolicyConflictError,
)
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.config_types.autotrader_defaults_config_types import (
    SessionEndDefaults,
)
from python.framework.validators.decision_logic_hook_validator import overrides_hook


def resolve_session_end_policy(
    config: AutoTraderConfig,
    broker_posture: str,
    decision_logic: Any,
    attended: bool,
) -> SessionEndDefaults:
    """
    Resolve and validate the session-end policy for one live session.

    Args:
        config: The loaded profile
        broker_posture: The broker's session_end_orders setting ('cancel' | 'leave')
        decision_logic: The session's decision logic — asked whether it accounts for an
            inherited book itself (#493)
        attended: A human DECLARED they are watching this start (`--attended` plus a TTY)

    Returns:
        The effective policy — raises rather than returning a policy it had to weaken
    """
    policy = config.session_end

    if policy.orders == 'leave' and broker_posture != 'leave':
        raise SessionEndPolicyConflictError(
            f"Profile '{config.name}' sets session_end.orders='leave', but "
            f"market_config.json has session_end_orders='{broker_posture}' for broker "
            f"'{config.broker_type}'. A profile may only TIGHTEN the session-end posture, "
            f'never loosen it — leaving resting orders at a venue after the process ends '
            f'is a deliberate change to market_config.json (or its user_configs override).'
        )

    if policy.positions == 'close':
        raise SessionEndCloseUnsupportedError(
            f"Profile '{config.name}' sets session_end.positions='close', which is not "
            f'built yet. A close that really reaches the venue is an asynchronous order — '
            f'the fill arrives on the next tick, and at session end the tick source is '
            f'already stopped — so it needs a synchronous drain with a timeout and the '
            f'unresolved-write resolution (#487). Until then the honest value is '
            f"'leave': the position stays at the venue and the report says so."
        )

    _check_adoption_pair(config, policy, decision_logic, attended)
    return policy


def _check_adoption_pair(
    config: AutoTraderConfig,
    policy: SessionEndDefaults,
    decision_logic: Any,
    attended: bool,
) -> None:
    """
    Refuse the pair that leaves orders at a venue the next boot will not adopt.

    Leaving resting orders behind is only useful if the successor may pick them up. With
    `adoption_mode='operator_confirm'` and nobody declared present, the next boot refuses
    and stays flat — so the orders sit at the venue and the bot meant to manage them does
    not start. It fires at 03:14 and it is invisible while writing the config, which is
    why it is checked here rather than discovered there.

    An `on_cold_start` that accounts for the inherited orders lifts exactly that refusal
    (#493), so a correctly built bot is NOT caught by this check.

    Args:
        config: The loaded profile
        policy: The resolved session-end policy
        decision_logic: The session's decision logic
        attended: A human declared they are watching this start

    Returns:
        None — raises SessionEndPolicyConflictError on an incoherent pair
    """
    if policy.orders != 'leave':
        return
    if not config.cold_start.enabled:
        raise SessionEndPolicyConflictError(
            f"Profile '{config.name}' sets session_end.orders='leave' while "
            f'cold_start.enabled=false. The orders would stay at the venue and no session '
            f'would ever adopt them back — nothing would manage them again.'
        )
    if config.cold_start.adoption_mode != 'operator_confirm':
        return
    if attended or overrides_hook(type(decision_logic), 'on_cold_start'):
        return

    raise SessionEndPolicyConflictError(
        f"Profile '{config.name}' sets session_end.orders='leave' together with "
        f"cold_start.adoption_mode='operator_confirm', and this start is unattended. The "
        f'orders would stay at the venue and the next boot would REFUSE to adopt them, '
        f'because it cannot ask anyone — so the bot that is meant to manage them would not '
        f'start. Three ways out: run attended (--attended from a terminal), set '
        f"cold_start.adoption_mode='auto' for unattended running, or give the decision "
        f'logic an on_cold_start that accounts for its inherited orders (#493).'
    )
