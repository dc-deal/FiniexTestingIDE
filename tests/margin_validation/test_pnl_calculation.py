"""
FiniexTestingIDE - P&L Calculation Tests (Margin Validation Suite)
Validates profit/loss calculations for successfully executed trades.

Tests:
- Trade record mathematical consistency
- Aggregation matches portfolio stats
- Fee breakdown correctness
"""

from tests.shared.shared_pnl import TestPnLCalculation, TestTradeRecordCompleteness

# All test classes imported from shared module.
# Pytest discovers them via this import.
