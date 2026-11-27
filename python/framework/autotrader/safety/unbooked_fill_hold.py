"""
FiniexTestingIDE - The Entry Hold Over a Fill the Book Does Not Hold (#652)

The start read the venue's closed orders since the carry-over was written and found a fill of ours
that the book does not hold — a session killed between a submit and its fill left the coins at the
venue — or could not finish the read. Opening now could buy the same position twice, so the session
starts and holds its entries: it manages and closes what is its own and opens nothing new. Its
resting entries are cancelled with the hold, as with every entry hold a start places.

Nothing in the session books the fill yet, so nothing can observe the cause go away: a start the
operator declared to watch (`--attended`) asks whether to lift the hold, after the operator has
reconciled the account by hand. An unattended start holds, and the carry-over keeps the write the
finding was measured against, so the next start finds the same fill again.
"""

from typing import Callable

from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.cold_start_types import BootRead
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.trading_env_types.entry_hold_types import EntryHoldLift, EntryHoldReason


def hold_entries_over_unbooked_fills(
    boot_read: BootRead,
    executor: LiveTradeExecutor,
    attended: bool,
    operator_alerts: OperatorAlerts,
    logger: AbstractLogger,
    ask: Callable[[str], str] = input,
) -> bool:
    """
    Hold the session's entries over what the boot read found, and ask an attended start to lift it.

    Args:
        boot_read: The read of the venue's closed orders since the carry-over — one that holds
        executor: The session's executor — its holds and its resting orders
        attended: Whether a person declared they watch this start, with a terminal to answer at
        operator_alerts: Where the hold and its lifting are reported out of band
        logger: The session logger
        ask: Reads the operator's answer to one question

    Returns:
        True while the hold stands after the start; False when the operator lifted it
    """
    detail = boot_read.describe()
    # Before the first market data the session's clock has not begun, so the hold dates from the
    # start rather than from a wall-clock reading a replay could never repeat
    at = executor.get_current_time_if_set()
    executor.get_entry_holds().hold(EntryHoldReason.UNBOOKED_FILL, at, detail)
    cancelled = executor.cancel_resting_entries()
    cancelled_line = (f'{len(cancelled)} resting entry order(s) of this bot cancelled: '
                      f"{', '.join(cancelled)}" if cancelled
                      else 'No resting entry order of this bot to cancel')
    logger.error(
        f'❌ Entries held: {detail}. The session manages and closes what is its own and opens '
        f'nothing new. {cancelled_line}; its protective exits stay. Only a start with --attended '
        f'lifts it, once the account is reconciled by hand.')
    operator_alerts.send(
        OperatorAlertKind.ENTRIES_HELD, OperatorAlertLevel.CRITICAL,
        'Entries held: a fill the book does not hold',
        f'{detail}.\nThe session runs: it manages and closes its position and opens nothing new. '
        f'{cancelled_line}.\nReconcile the account by hand, then start once with --attended to '
        f'lift the hold.')

    if not attended:
        return True
    answer = ask(
        f'\n  Entries are held: {detail}.\n'
        f'  Lift the hold? Answer yes only once the account is reconciled by hand — the book will '
        f'still not hold these fills. [y/N] ')
    if answer.strip().lower() not in ('y', 'yes'):
        logger.info('Entries stay held: the operator did not lift the hold at the start.')
        return True

    executor.get_entry_holds().lift(EntryHoldReason.UNBOOKED_FILL, EntryHoldLift.OPERATOR, at)
    logger.warning(
        f'⚠️ Entries lifted by the operator at the start, over: {detail}. The book still does not '
        f'hold these fills, and the next start measures against this start\'s write.')
    operator_alerts.send(
        OperatorAlertKind.ENTRIES_LIFTED, OperatorAlertLevel.INFO,
        'Entries lifted: a fill the book does not hold',
        f'The operator lifted the hold at the start, over: {detail}.')
    return False
