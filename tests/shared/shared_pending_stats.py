"""
FiniexTestingIDE - Shared Pending-Order Counter Tests
Reusable test classes for the pending-order counters a scenario's order-event stream yields (#362).

Validates:
- The counters add up to the submissions — a row with an ending missing disproves itself
- Answer durations (avg/min/max) are populated
- Every arrival at the simulated venue is an acceptance
- An order the data's end met on its way is counted expired, never as unconfirmed

Used by: pending_stats test suite
Import these classes into suite-specific test_pending_stats.py files.
"""

from python.framework.types.api.report_types import PendingOrdersUnitRow
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats


class TestPendingCountersBaseline:
    """The counters a backtest's stream yields — baseline assertions."""

    def test_the_counters_are_populated(self, pending_row: PendingOrdersUnitRow):
        """The scenario submitted orders, so it has a row with submissions."""
        assert pending_row is not None
        assert pending_row.total_submitted > 0, 'No order was submitted'

    def test_the_counters_add_up_to_the_submissions(self, pending_row: PendingOrdersUnitRow):
        """Every submission's in-flight phase ended exactly one way."""
        ended = (pending_row.total_accepted + pending_row.total_rejected
                 + pending_row.total_never_confirmed + pending_row.total_expired)
        assert pending_row.total_submitted == ended, (
            f'{pending_row.total_submitted} submitted, but accepted '
            f'({pending_row.total_accepted}) + rejected ({pending_row.total_rejected}) + '
            f'never confirmed ({pending_row.total_never_confirmed}) + expired '
            f'({pending_row.total_expired}) = {ended}')

    def test_no_order_is_rejected(self, pending_row: PendingOrdersUnitRow):
        """No orders should be rejected in normal backtesting."""
        assert pending_row.total_rejected == 0, (
            f'Unexpected rejections: {pending_row.total_rejected}')

    def test_a_simulated_venue_confirms_everything(self, pending_row: PendingOrdersUnitRow):
        """Only a real venue can leave an order unconfirmed."""
        assert pending_row.total_never_confirmed == 0
        assert pending_row.never_confirmed_orders == []

    def test_answer_durations_are_populated(self, pending_row: PendingOrdersUnitRow):
        """The modelled delay of every answer is a sample."""
        assert pending_row.avg_in_flight_ms > 0, 'avg_in_flight_ms not set'
        assert pending_row.min_in_flight_ms is not None, 'min_in_flight_ms not set'
        assert pending_row.max_in_flight_ms is not None, 'max_in_flight_ms not set'
        assert pending_row.min_in_flight_ms <= pending_row.max_in_flight_ms

    def test_the_average_lies_between_min_and_max(self, pending_row: PendingOrdersUnitRow):
        """Average duration should be between min and max."""
        assert pending_row.min_in_flight_ms <= pending_row.avg_in_flight_ms
        assert pending_row.avg_in_flight_ms <= pending_row.max_in_flight_ms


class TestEveryArrivalIsAnAcceptance:
    """The simulated venue takes every order that reaches it — open and close alike."""

    def test_accepted_covers_the_completed_trades(
        self,
        pending_row: PendingOrdersUnitRow,
        portfolio_stats: PortfolioStats
    ):
        """
        Each completed trade passed the pipeline twice: its open and its close were accepted.

        There are no end-of-scenario synthetic closes any more (#492).
        """
        completed_trades = portfolio_stats.total_trades
        assert pending_row.total_accepted >= 2 * completed_trades, (
            f'total_accepted ({pending_row.total_accepted}) < '
            f'2 x total_trades ({completed_trades})')


class TestAnOrderTheDataEndMet:
    """An order still on its way when the data ends expired there — it is no anomaly."""

    def test_it_is_counted_expired(self, pending_row: PendingOrdersUnitRow):
        """
        Trade 2 opens at tick 5000 (last tick). No later tick exists to deliver it, so the
        data's end meets it on its way.
        """
        assert pending_row.total_expired >= 1, (
            f'Expected at least 1 expired on the way, got {pending_row.total_expired}')

    def test_it_is_not_listed_as_unconfirmed(self, pending_row: PendingOrdersUnitRow):
        """The list holds only what a venue never confirmed — nothing here."""
        assert pending_row.never_confirmed_orders == []
