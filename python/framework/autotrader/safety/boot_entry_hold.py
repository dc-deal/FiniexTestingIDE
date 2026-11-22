"""
FiniexTestingIDE - The Entry Hold a Start Places (#499)

The cold start found orders of another owner on this bot's instrument while the profile declares the
account exclusive. The session starts — refusing would abandon its exposure at the venue — and holds
its entries: it manages and closes what is its own and opens nothing new. Its resting entries are
cancelled with the hold, because one the venue fills while entries are held is new risk through the
back door; its protective exits stay.

A start the operator declared to watch (`--attended`) asks whether to lift the hold. A yes accepts those
orders as another owner's for good: the carry-over remembers them, and the next start, attended or not,
holds nothing over them. An unattended start never asks — it holds until the orders are gone.
"""

from datetime import datetime, timezone
from typing import Callable, Optional

from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.cold_start_types import BootPremiseViolation
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.persistence_types import AcceptedForeignOrders
from python.framework.types.trading_env_types.entry_hold_types import EntryHoldLift, EntryHoldReason


def hold_entries_at_boot(
    violation: BootPremiseViolation,
    executor: LiveTradeExecutor,
    attended: bool,
    reconciled: bool,
    operator_alerts: OperatorAlerts,
    logger: AbstractLogger,
    ask: Callable[[str], str] = input,
) -> Optional[AcceptedForeignOrders]:
    """
    Hold the session's entries over what the start found, and ask an attended start to lift it.

    Args:
        violation: The orders of another owner on this bot's instrument
        executor: The session's executor — its holds and its resting orders
        attended: Whether a person declared they watch this start, with a terminal to answer at
        reconciled: Whether the session reconciles with the venue — without it nothing observes the
            orders going, and only an attended start lifts the hold
        operator_alerts: Where the hold and its lifting are reported out of band
        logger: The session logger
        ask: Reads the operator's answer to one question

    Returns:
        The orders the operator accepted, for the carry-over; None when the hold stands
    """
    # Before the first market data the session's clock has not begun, so the hold dates from the
    # start rather than from a wall-clock reading a replay could never repeat
    at = executor.get_current_time_if_set()
    executor.get_entry_holds().hold(EntryHoldReason.BOOT_PREMISE, at, violation.detail)
    cancelled = executor.cancel_resting_entries()
    cancelled_line = (f'{len(cancelled)} resting entry order(s) of this bot cancelled: '
                      f"{', '.join(cancelled)}" if cancelled else 'No resting entry order of this bot to cancel')
    logger.error(
        f'❌ Entries held: {violation.detail}. The session manages and closes what is its own and '
        f'opens nothing new. {cancelled_line}; its protective exits stay.')
    lifts = ('It lifts by itself once those orders are gone.' if reconciled else
             'Nothing reconciles this session with the venue, so only a start with --attended lifts it.')
    operator_alerts.send(
        OperatorAlertKind.ENTRIES_HELD, OperatorAlertLevel.CRITICAL,
        f'Entries held on {violation.symbol}',
        f'{violation.detail}.\nThe session runs: it manages and closes its position and opens '
        f'nothing new. {cancelled_line}.\n{lifts}')

    if not attended:
        return None
    answer = ask(
        f'\n  Entries are held: {violation.detail}.\n'
        f'  Lift the hold and accept these orders as another owner\'s on {violation.symbol}? '
        f'The next start will not hold over them again. [y/N] ')
    if answer.strip().lower() not in ('y', 'yes'):
        logger.info('Entries stay held: the operator did not lift the hold at the start.')
        return None

    executor.get_entry_holds().lift(EntryHoldReason.BOOT_PREMISE, EntryHoldLift.OPERATOR, at)
    logger.warning(
        f'⚠️ Entries lifted by the operator at the start: {", ".join(violation.foreign_refs)} on '
        f'{violation.symbol} are accepted as another owner\'s, and the next start holds nothing over '
        f'them. The account is still declared exclusive while it is not — every account-level limit '
        f'measures a denominator that is not this bot\'s alone.')
    operator_alerts.send(
        OperatorAlertKind.ENTRIES_LIFTED, OperatorAlertLevel.INFO,
        f'Entries lifted on {violation.symbol}',
        f'The operator accepted {", ".join(violation.foreign_refs)} as another owner\'s at the start.')
    # The carry-over holds one set, so the orders accepted at an earlier start that still rest stay
    # in it beside the new ones. When the operator accepted them: our own observation, which nothing
    # decides on
    return AcceptedForeignOrders(broker_refs=list(violation.accepted_refs) + list(violation.foreign_refs),
                                 accepted_at_utc=datetime.now(timezone.utc).isoformat())
