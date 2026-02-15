"""
FiniexTestingIDE - Trade Execution Tests
Validates trade execution matches expected sequence

Tests:
- Trade count matches expected
- Trade directions match config
- Orders executed without rejection
"""

from tests.shared.shared_execution import TestTradeExecution

# All test classes imported from shared module.
# Pytest discovers them via this import.
