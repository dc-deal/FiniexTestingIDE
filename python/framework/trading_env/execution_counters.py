"""
FiniexTestingIDE - Execution Counters

The executor's order counts, kept over two spans at once: the run, and the booking period that is
running (#476). Every count is taken once and reaches both, so a period's counts are counted inside
the period — never the difference of two run totals — and the periods of a run add up to its
totals by construction.

What each count counts is declared on `ExecutionStats`; which status is counted where, by
`EXECUTION_STATS_FIELD_BY_STATUS`.
"""

import dataclasses

from python.framework.types.trading_env_types.order_types import OrderStatus
from python.framework.types.trading_env_types.trading_env_stats_types import (
    EXECUTION_STATS_FIELD_BY_STATUS,
    ExecutionStats,
)


class ExecutionCounters:
    """
    Order counts over the run and over the running booking period.
    """

    def __init__(self):
        self._run = ExecutionStats()
        self._period = ExecutionStats()

    def count_submission(self) -> None:
        """Count one order handed to the venue."""
        self._count('orders_submitted')

    def count_adoption(self) -> None:
        """Count one order a previous session sent, taken over by this one."""
        self._count('orders_adopted')

    def count_status(self, status: OrderStatus) -> None:
        """
        Count one booked order-history row by the status it ended with.

        Args:
            status: The row's status; a status declared uncounted is not counted
        """
        field_name = EXECUTION_STATS_FIELD_BY_STATUS[status]
        if field_name is None:
            return
        self._count(field_name)

    def count_protective_close(self) -> None:
        """Count one close a stop-loss or take-profit triggered."""
        self._count('sl_tp_triggered')

    def get_run_stats(self) -> ExecutionStats:
        """
        The counts over the run so far.

        Returns:
            A copy, so a caller cannot move the counts
        """
        return dataclasses.replace(self._run)

    def take_period_stats(self) -> ExecutionStats:
        """
        The counts of the booking period that is ending, and a fresh count for the next one.

        Called once per filed period, where the period is sealed.

        Returns:
            The ending period's counts
        """
        period = self._period
        self._period = ExecutionStats()
        return period

    def _count(self, field_name: str) -> None:
        """
        Count one in a field, over the run and over the period.

        Args:
            field_name: The ExecutionStats field
        """
        setattr(self._run, field_name, getattr(self._run, field_name) + 1)
        setattr(self._period, field_name, getattr(self._period, field_name) + 1)
