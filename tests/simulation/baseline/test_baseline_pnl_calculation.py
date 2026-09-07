"""
FiniexTestingIDE - P&L Calculation Tests
Validates profit/loss calculations via internal consistency checks.

Tests:
- Trade record mathematical consistency
- Aggregation matches portfolio stats
- Fee breakdown correctness
"""

from tests.shared.shared_pnl import TestPnLCalculation, TestTradeRecordCompleteness

# All test classes imported from shared module.
# Pytest discovers them via this import.
