"""
FiniexTestingIDE - Pending-Order Counter Tests
Imports shared test classes from tests/shared/shared_pending_stats.py

Uses pending_stats_validation_test.json scenario config:
- Trade 1: Normal trade (tick 10-110) — validates happy path
- Trade 2: Entry on the last tick (5000) — its order is still in flight at the end, which
  validates that the data's end counts it expired on the way
"""

from tests.shared.shared_batch_health import TestBatchHealth
from tests.shared.shared_pending_stats import (
    TestAnOrderTheDataEndMet,
    TestEveryArrivalIsAnAcceptance,
    TestPendingCountersBaseline,
)
