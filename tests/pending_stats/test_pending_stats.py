"""
FiniexTestingIDE - Pending Order Statistics Tests
Imports shared test classes from tests/shared/shared_pending_stats.py

Uses pending_stats_validation_test.json scenario config:
- Trade 1: Normal trade (tick 10-110) — validates happy path
- Trade 2: Late trade (tick 4990, close at 4993) — validates force-closed detection
"""

from tests.shared.shared_pending_stats import (
    TestPendingStatsBaseline,
    TestSyntheticCloseNotCounted,
    TestForceClosedDetection,
)
