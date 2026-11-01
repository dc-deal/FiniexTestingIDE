"""
FiniexTestingIDE - Stop Budget Check (#630)

Holds a session's stop settings against what the session runs under, at its start. A supervisor
kills a stop after a time it never tells the process, so a stop whose waits do not fit under that
time loses its last part without a word, and nobody finds out before the first stop in an outage.

A warning on the session's record and never a refusal: a stop that may run out of time is still
no reason not to trade, and a session refused over it would be a restart nobody asked for.
"""

from typing import List

from python.framework.types.config_types.autotrader_defaults_config_types import ShutdownDefaults
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
)

# Stable ids of the findings a start can leave on its record. They carry `shutdown` and not `stop`:
# they are served, and there the bare word is the stop order's.
_WAITS_BEYOND_KILL_CHECK = 'shutdown_waits_beyond_kill'
_KILL_UNDECLARED_CHECK = 'shutdown_kill_undeclared'
_ORDER_WAIT_BELOW_POLL_CHECK = 'shutdown_order_wait_below_poll'

# What an order's second status read needs on top of the broker's poll interval
_SECOND_READ_MARGIN_S = 1.0


def check_stop_budget(
    settings: ShutdownDefaults,
    poll_interval_ms: int,
    live_session: bool,
    attended: bool,
    scope: str,
) -> List[ValidationFinding]:
    """
    Advisory findings where a session's stop cannot keep what its settings promise.

    The kill is held against the waits wherever one is declared, because the waits are the same in
    a mock session. The other two questions mean nothing for a mock session: it has no supervisor
    to declare, and a mock venue answers without a poll interval to miss.

    Args:
        settings: The session's stop settings
        poll_interval_ms: The broker's interval between two status reads of one order
        live_session: Whether the session runs the live adapter, a dry run included
        attended: Whether a human declared they are watching this start
        scope: The unit the findings concern

    Returns:
        One finding per question that came out wrong — empty when none did
    """
    findings = []
    kill_after_s = settings.supervisor_kill_after_s
    if kill_after_s is not None:
        waits_s = settings.bounded_waits_s()
        needed_s = waits_s + settings.order_wait_s + settings.reserve_for_records_s
        if needed_s > kill_after_s:
            findings.append(_finding(
                _WAITS_BEYOND_KILL_CHECK,
                f"The waits of this session's stop do not fit its declared kill: joins and alert "
                f'flush {waits_s:g} s + order wait {settings.order_wait_s:g} s + reserve for the '
                f'records {settings.reserve_for_records_s:g} s = {needed_s:g} s, '
                f'supervisor_kill_after_s {kill_after_s:g} s. The wait for orders on their way is '
                f'cut short to fit, and what still overruns dies with the process. With less '
                f'than {settings.reserve_for_records_s:g} s plus one socket timeout (15 s by '
                f'default) left, no cancel and no end read goes out at all, so resting orders stay '
                f'at the venue. Shorten autotrader.shutdown or give the supervisor more time.',
                scope))

    if not live_session:
        return findings

    if kill_after_s is None and not attended:
        findings.append(_finding(
            _KILL_UNDECLARED_CHECK,
            'This session runs unattended and declares no supervisor_kill_after_s, so its stop '
            'has no budget: with the venue unreachable a supervisor can kill it before the ledger '
            'row is written. Declare the time the supervisor gives the process after its signal '
            '(for instance the -t of docker stop) in autotrader.shutdown.',
            scope))

    smallest_wait_s = poll_interval_ms / 1000.0 + _SECOND_READ_MARGIN_S
    if settings.order_wait_s < smallest_wait_s:
        findings.append(_finding(
            _ORDER_WAIT_BELOW_POLL_CHECK,
            f"order_wait_s {settings.order_wait_s:g} s is below the broker's poll_interval_ms "
            f'({poll_interval_ms} ms) plus a second: a stop can end its wait before an order\'s '
            f'second status read and book as unaccounted an order the venue has already answered. '
            f'Set it to {smallest_wait_s:g} s or more in autotrader.shutdown.',
            scope))
    return findings


def _finding(check: str, message: str, scope: str) -> ValidationFinding:
    """
    Build one advisory finding about the session's stop.

    Args:
        check: The stable id of the question
        message: Operator-readable text
        scope: The unit the finding concerns

    Returns:
        The finding, a warning
    """
    return ValidationFinding(Severity.WARNING, check, ValidationDomain.SETUP, message, scope)
