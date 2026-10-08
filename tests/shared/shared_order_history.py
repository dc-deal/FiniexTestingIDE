"""
FiniexTestingIDE - Shared Order History Tests
Reusable test class for order_history validation across test suites.

Validates:
- order_history is populated after scenario execution
- every status count in execution_stats equals the rows of that status
- every executed entry carries an executed_price
- every refused entry carries a valid RejectionReason

Used by: baseline
Import this class into suite-specific test_order_history.py files.
"""

from typing import List

from python.framework.types.process_data_types import ProcessTickLoopResult
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.types.trading_env_types.trading_env_stats_types import (
    EXECUTION_STATS_FIELD_BY_STATUS,
)


class TestOrderHistoryBaseline:
    """Tests for order history — baseline assertions."""

    def test_order_history_not_none(self, order_history: List[OrderResult]):
        """order_history must be populated after scenario execution."""
        assert order_history is not None
        assert len(order_history) > 0, 'order_history is empty'

    def test_order_history_count_matches_stats(
        self,
        order_history: List[OrderResult],
        tick_loop_results: ProcessTickLoopResult
    ):
        """
        Every status count in execution_stats equals the rows of that status, exactly.

        The counts are taken where each row is booked, so as long as the history is not
        capped the two are the same set counted twice (#362). This used to hold only for
        rejections, and only one way for fills: a fill counted executed rows of opens but
        not of closes, and a refused amend counted as a rejected order.
        """
        stats = tick_loop_results.execution_stats
        for status, field_name in EXECUTION_STATS_FIELD_BY_STATUS.items():
            if field_name is None:
                continue
            rows = sum(1 for e in order_history if e.status is status)
            assert rows == getattr(stats, field_name), (
                f'{status.value} rows in order_history ({rows}) != '
                f'{field_name} ({getattr(stats, field_name)})'
            )

    def test_order_history_executed_have_price(self, order_history: List[OrderResult]):
        """Every executed entry must carry an executed_price."""
        for entry in order_history:
            if entry.is_success:
                assert entry.executed_price is not None, (
                    f'Executed order {entry.order_id} has no executed_price'
                )
                assert entry.executed_price > 0, (
                    f'Executed order {entry.order_id} has non-positive executed_price '
                    f'({entry.executed_price})'
                )

    def test_order_history_rejection_reasons(self, order_history: List[OrderResult]):
        """Every refused entry — denied or rejected — must carry a valid RejectionReason."""
        for entry in order_history:
            if entry.is_refused:
                assert entry.rejection_reason is not None, (
                    f'Rejected order {entry.order_id} has no rejection_reason'
                )
