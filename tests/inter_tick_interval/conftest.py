"""
Inter-Tick Interval Test Fixtures
==================================
Shared fixtures and helpers for inter-tick interval profiling tests.

No file I/O, no external data — pure mock-based testing.
"""

import pytest
from datetime import datetime, timedelta, timezone
from typing import List

from python.framework.types.performance_types.performance_metrics_types import (
    InterTickIntervalStats,
)


# =============================================================================
# TIME HELPERS
# =============================================================================

def utc(year: int, month: int, day: int,
        hour: int = 0, minute: int = 0, second: int = 0,
        ms: int = 0) -> datetime:
    """Create timezone-aware UTC datetime with millisecond precision."""
    return datetime(year, month, day, hour, minute, second,
                    ms * 1000, tzinfo=timezone.utc)


def make_intervals_from_timestamps(timestamps: List[datetime]) -> List[float]:
    """
    Compute inter-tick intervals in ms from a list of timestamps.

    Args:
        timestamps: Ordered list of tick timestamps

    Returns:
        List of intervals in milliseconds (len = len(timestamps) - 1)
    """
    intervals = []
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i - 1]).total_seconds() * 1000
        intervals.append(delta)
    return intervals


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def regular_intervals() -> List[float]:
    """10 regular intervals of 100ms each."""
    return [100.0] * 10


@pytest.fixture
def mixed_intervals() -> List[float]:
    """Mixed intervals with known distribution."""
    return [
        5.0, 10.0, 20.0, 30.0, 50.0,
        80.0, 100.0, 150.0, 200.0, 500.0
    ]


@pytest.fixture
def intervals_with_gaps() -> List[float]:
    """Intervals including session gaps (> 300s = 300000ms)."""
    return [
        10.0, 20.0, 50.0, 100.0,
        400000.0,  # 400s gap — should be filtered
        15.0, 25.0, 60.0, 90.0,
        600000.0,  # 600s gap — should be filtered
        30.0, 40.0
    ]
