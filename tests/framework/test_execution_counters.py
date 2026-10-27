"""
The executor's order counts over the run and over the running booking period (#476): every count
reaches both, a period's counts are taken once at its seal and start afresh, and the run's go on —
so a run's periods add up to its execution statistics.
"""

from python.framework.trading_env.execution_counters import ExecutionCounters
from python.framework.types.trading_env_types.order_types import OrderStatus
from python.framework.types.trading_env_types.trading_env_stats_types import (
    EXECUTION_STATS_FIELD_BY_STATUS,
    ExecutionStats,
)


class TestACount:

    def test_reaches_the_run_and_the_period(self):
        counters = ExecutionCounters()
        counters.count_submission()
        counters.count_adoption()
        counters.count_protective_close()

        counted = ExecutionStats(orders_submitted=1, orders_adopted=1, sl_tp_triggered=1)
        assert counters.get_run_stats() == counted
        assert counters.take_period_stats() == counted

    def test_every_status_is_counted_in_its_own_field(self):
        for status, field_name in EXECUTION_STATS_FIELD_BY_STATUS.items():
            counters = ExecutionCounters()
            counters.count_status(status)
            expected = ExecutionStats(**({field_name: 1} if field_name else {}))
            assert counters.get_run_stats() == expected, status

    def test_a_pending_row_is_no_ending_and_counts_nothing(self):
        counters = ExecutionCounters()
        counters.count_status(OrderStatus.PENDING)

        assert counters.get_run_stats() == ExecutionStats()


class TestAPeriod:

    def test_is_taken_once_and_starts_afresh(self):
        counters = ExecutionCounters()
        counters.count_submission()
        assert counters.take_period_stats() == ExecutionStats(orders_submitted=1)
        assert counters.take_period_stats() == ExecutionStats()

    def test_leaves_the_run_counting_on(self):
        counters = ExecutionCounters()
        counters.count_submission()
        first = counters.take_period_stats()
        counters.count_submission()
        counters.count_status(OrderStatus.EXECUTED)
        second = counters.take_period_stats()

        assert counters.get_run_stats() == ExecutionStats(orders_submitted=2, orders_executed=1)
        assert first.orders_submitted + second.orders_submitted == 2

    def test_a_read_of_the_run_cannot_move_its_counts(self):
        counters = ExecutionCounters()
        counters.get_run_stats().orders_submitted = 99

        assert counters.get_run_stats() == ExecutionStats()
