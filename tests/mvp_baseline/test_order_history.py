"""
FiniexTestingIDE - Order History Tests
Validates order_history contents and consistency with execution_stats.

Tests:
- order_history is populated (not None, not empty)
- entry count matches execution_stats counters
- executed entries carry executed_price
- rejected entries carry a valid RejectionReason
"""

from tests.shared.shared_order_history import TestOrderHistoryBaseline

# All test classes imported from shared module.
# Pytest discovers them via this import.
