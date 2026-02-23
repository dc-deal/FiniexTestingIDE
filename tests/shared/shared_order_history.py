"""
FiniexTestingIDE - Shared Order History Tests
Reusable test class for order_history validation across test suites.

Validates:
- order_history is populated after scenario execution
- entry count is consistent with execution_stats counters
- every executed entry carries an executed_price
- every rejected entry carries a valid RejectionReason

Used by: mvp_baseline
Import this class into suite-specific test_order_history.py files.
"""

from typing import List

from python.framework.types.order_types import OrderResult
from python.framework.types.process_data_types import ProcessTickLoopResult


class TestOrderHistoryBaseline:
    """Tests for order history — baseline assertions."""

    def test_order_history_not_none(self, order_history: List[OrderResult]):
        """order_history must be populated after scenario execution."""
        assert order_history is not None
        assert len(order_history) > 0, "order_history is empty"

    def test_order_history_count_matches_stats(
        self,
        order_history: List[OrderResult],
        tick_loop_results: ProcessTickLoopResult
    ):
        """
        order_history entry counts must be consistent with execution_stats.

        order_history contains three types of entries per trade:
        - PENDING: one per open_order() submission
        - EXECUTED (open): one per fill in _fill_open_order()
        - EXECUTED (close): one per fill in _fill_close_order()
        Therefore order_history is always larger than orders_executed alone.
        Assertions:
        - rejected entries == orders_rejected (exact)
        - executed entries >= orders_executed (close fills add extra entries)
        """
        stats = tick_loop_results.execution_stats
        rejected_count = sum(1 for e in order_history if e.is_rejected)
        executed_count = sum(1 for e in order_history if e.is_success)
        assert rejected_count == stats.orders_rejected, (
            f"Rejected entries in order_history ({rejected_count}) != "
            f"orders_rejected ({stats.orders_rejected})"
        )
        assert executed_count >= stats.orders_executed, (
            f"Executed entries in order_history ({executed_count}) < "
            f"orders_executed ({stats.orders_executed})"
        )

    def test_order_history_executed_have_price(self, order_history: List[OrderResult]):
        """Every executed entry must carry an executed_price."""
        for entry in order_history:
            if entry.is_success:
                assert entry.executed_price is not None, (
                    f"Executed order {entry.order_id} has no executed_price"
                )
                assert entry.executed_price > 0, (
                    f"Executed order {entry.order_id} has non-positive executed_price "
                    f"({entry.executed_price})"
                )

    def test_order_history_rejection_reasons(self, order_history: List[OrderResult]):
        """Every rejected entry must carry a valid RejectionReason."""
        for entry in order_history:
            if entry.is_rejected:
                assert entry.rejection_reason is not None, (
                    f"Rejected order {entry.order_id} has no rejection_reason"
                )
