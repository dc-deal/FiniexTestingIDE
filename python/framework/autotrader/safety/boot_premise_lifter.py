"""
FiniexTestingIDE - Lifting a Boot-Premise Hold When Its Orders Are Gone (#499)

The start held the session's entries over orders of another owner on this bot's instrument. The hold
lifts by itself when that cause measurably goes away: a reconcile cycle that COMPLETED shows none of
those orders resting any more. Time alone lifts nothing, and neither does a cycle that could not read
the venue — it proves nothing about what rests there. An order of another owner that appears later is
a divergence the reconcile reports, not the cause this hold was placed over.
"""

from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.cold_start_types import BootPremiseViolation
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.live_types.reconciliation_types import ReconciliationResult
from python.framework.types.trading_env_types.entry_hold_types import EntryHoldLift, EntryHoldReason


class BootPremiseLifter:
    """
    Watches the reconcile cycles of a session whose start held its entries, and lifts the hold.

    Args:
        violation: What the start held the entries over
        executor: The session's executor, whose holds it lifts
        operator_alerts: Where the lifting is reported out of band
        logger: The session logger
    """

    def __init__(self, violation: BootPremiseViolation, executor: LiveTradeExecutor,
                 operator_alerts: OperatorAlerts, logger: AbstractLogger):
        self._violation = violation
        self._executor = executor
        self._operator_alerts = operator_alerts
        self._logger = logger

    def observe(self, result: ReconciliationResult) -> None:
        """
        Lift the hold when a completed cycle shows none of its orders resting any more.

        Args:
            result: One reconcile cycle's answer
        """
        holds = self._executor.get_entry_holds()
        if not holds.is_held(EntryHoldReason.BOOT_PREMISE):
            return
        if result.skipped_reason is not None:
            return
        resting = {order.broker_ref for order in result.ghost_orders
                   if order.symbol == self._violation.symbol}
        if resting & set(self._violation.foreign_refs):
            return
        holds.lift(EntryHoldReason.BOOT_PREMISE, EntryHoldLift.OBSERVED,
                   self._executor.get_current_time_if_set())
        refs = ', '.join(self._violation.foreign_refs)
        self._logger.info(
            f'✅ Entries lifted: the orders of another owner the start held them over ({refs}) no '
            f'longer rest on {self._violation.symbol}, as a completed reconcile cycle shows.')
        self._operator_alerts.send(
            OperatorAlertKind.ENTRIES_LIFTED, OperatorAlertLevel.INFO,
            f'Entries lifted on {self._violation.symbol}',
            f'The orders of another owner the start held them over ({refs}) are gone; the session '
            f'opens positions again.')
