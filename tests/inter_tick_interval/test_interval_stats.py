"""
Inter-Tick Interval Stats Tests
================================
Tests for InterTickIntervalStats computation via ProfilingData.

Covers:
- Stats computation from known distributions
- Gap filtering with configurable threshold
- Edge cases (empty, single tick, all gaps)
- Integration with ProfilingData.from_dicts()
"""

import pytest
import numpy as np
from collections import defaultdict

from python.framework.types.scenario_types.scenario_set_performance_types import (
    ProfilingData,
)
from python.framework.types.performance_types.performance_metrics_types import (
    InterTickIntervalStats,
)
from tests.inter_tick_interval.conftest import make_intervals_from_timestamps, utc


# =============================================================================
# STATS COMPUTATION
# =============================================================================

class TestIntervalStatsComputation:
    """Test distribution statistics from known interval data."""

    def test_regular_intervals(self, regular_intervals):
        """All identical intervals — all stats should equal the interval value."""
        stats = ProfilingData._compute_interval_stats(regular_intervals, 300.0)

        assert stats is not None
        assert stats.min_ms == 100.0
        assert stats.max_ms == 100.0
        assert stats.mean_ms == 100.0
        assert stats.median_ms == 100.0
        assert stats.p5_ms == 100.0
        assert stats.p95_ms == 100.0
        assert stats.total_intervals == 10
        assert stats.filtered_intervals == 10
        assert stats.gaps_removed == 0

    def test_mixed_intervals(self, mixed_intervals):
        """Known distribution — verify percentiles match numpy."""
        stats = ProfilingData._compute_interval_stats(mixed_intervals, 300.0)

        assert stats is not None
        arr = np.array(mixed_intervals)
        assert stats.min_ms == pytest.approx(np.min(arr))
        assert stats.max_ms == pytest.approx(np.max(arr))
        assert stats.mean_ms == pytest.approx(np.mean(arr))
        assert stats.median_ms == pytest.approx(np.median(arr))
        assert stats.p5_ms == pytest.approx(float(np.percentile(arr, 5)))
        assert stats.p95_ms == pytest.approx(float(np.percentile(arr, 95)))
        assert stats.total_intervals == 10
        assert stats.filtered_intervals == 10
        assert stats.gaps_removed == 0

    def test_single_interval(self):
        """Single interval — all stats equal that value."""
        stats = ProfilingData._compute_interval_stats([42.0], 300.0)

        assert stats is not None
        assert stats.min_ms == 42.0
        assert stats.max_ms == 42.0
        assert stats.mean_ms == 42.0
        assert stats.median_ms == 42.0
        assert stats.total_intervals == 1
        assert stats.filtered_intervals == 1

    def test_two_intervals(self):
        """Two intervals — verify min/max and percentiles."""
        stats = ProfilingData._compute_interval_stats([10.0, 200.0], 300.0)

        assert stats is not None
        assert stats.min_ms == 10.0
        assert stats.max_ms == 200.0
        assert stats.mean_ms == pytest.approx(105.0)


# =============================================================================
# GAP FILTERING
# =============================================================================

class TestGapFiltering:
    """Test session/weekend gap removal."""

    def test_gaps_filtered_at_default_threshold(self, intervals_with_gaps):
        """Gaps > 300s threshold should be removed."""
        stats = ProfilingData._compute_interval_stats(
            intervals_with_gaps, 300.0
        )

        assert stats is not None
        assert stats.gaps_removed == 2
        assert stats.total_intervals == 12
        assert stats.filtered_intervals == 10

    def test_custom_threshold(self, intervals_with_gaps):
        """Custom threshold — only gaps > 500s removed."""
        stats = ProfilingData._compute_interval_stats(
            intervals_with_gaps, 500.0
        )

        assert stats is not None
        # 400s gap passes, 600s gap filtered
        assert stats.gaps_removed == 1
        assert stats.filtered_intervals == 11

    def test_strict_threshold_filters_all(self):
        """Threshold so low that all intervals are filtered."""
        intervals = [100.0, 200.0, 300.0]
        stats = ProfilingData._compute_interval_stats(intervals, 0.05)

        assert stats is None

    def test_gap_threshold_stored(self, intervals_with_gaps):
        """Gap threshold value is stored in stats for reference."""
        stats = ProfilingData._compute_interval_stats(
            intervals_with_gaps, 300.0
        )

        assert stats.gap_threshold_s == 300.0


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Test boundary conditions."""

    def test_empty_intervals(self):
        """Empty list — should return None."""
        stats = ProfilingData._compute_interval_stats([], 300.0)
        assert stats is None

    def test_none_intervals_in_from_dicts(self):
        """from_dicts with no intervals — interval_stats should be None."""
        times = defaultdict(float)
        times['worker_decision'] = 100.0
        times['total_per_tick'] = 200.0
        counts = defaultdict(int)
        counts['worker_decision'] = 10

        profiling = ProfilingData.from_dicts(
            dict(times), dict(counts),
            inter_tick_intervals_ms=None
        )

        assert profiling.interval_stats is None

    def test_all_gaps_returns_none(self):
        """All intervals exceed threshold — should return None."""
        intervals = [500000.0, 600000.0]  # 500s, 600s
        stats = ProfilingData._compute_interval_stats(intervals, 300.0)
        assert stats is None


# =============================================================================
# FROM_DICTS INTEGRATION
# =============================================================================

class TestFromDictsIntegration:
    """Test interval stats integration with ProfilingData.from_dicts()."""

    def test_intervals_flow_through(self, mixed_intervals):
        """Intervals passed to from_dicts produce valid stats."""
        times = defaultdict(float)
        times['worker_decision'] = 100.0
        times['total_per_tick'] = 200.0
        counts = defaultdict(int)
        counts['worker_decision'] = 10

        profiling = ProfilingData.from_dicts(
            dict(times), dict(counts),
            inter_tick_intervals_ms=mixed_intervals,
            gap_threshold_s=300.0
        )

        assert profiling.interval_stats is not None
        assert profiling.interval_stats.total_intervals == 10
        assert profiling.interval_stats.filtered_intervals == 10
        assert profiling.interval_stats.min_ms == 5.0
        assert profiling.interval_stats.max_ms == 500.0

    def test_operations_still_work_with_intervals(self, mixed_intervals):
        """Operations dict is unaffected by interval processing."""
        times = defaultdict(float)
        times['worker_decision'] = 100.0
        times['bar_rendering'] = 50.0
        times['total_per_tick'] = 200.0
        counts = defaultdict(int)
        counts['worker_decision'] = 10
        counts['bar_rendering'] = 10

        profiling = ProfilingData.from_dicts(
            dict(times), dict(counts),
            inter_tick_intervals_ms=mixed_intervals
        )

        assert 'worker_decision' in profiling.operations
        assert 'bar_rendering' in profiling.operations
        assert profiling.operations['worker_decision'].total_time_ms == 100.0
        assert profiling.total_per_tick_ms == 200.0

    def test_backward_compatible_without_intervals(self):
        """from_dicts without interval args works as before."""
        times = defaultdict(float)
        times['worker_decision'] = 100.0
        times['total_per_tick'] = 200.0
        counts = defaultdict(int)
        counts['worker_decision'] = 10

        profiling = ProfilingData.from_dicts(dict(times), dict(counts))

        assert profiling.interval_stats is None
        assert 'worker_decision' in profiling.operations
        assert profiling.total_per_tick_ms == 200.0


# =============================================================================
# TIMESTAMP-BASED INTERVAL COLLECTION
# =============================================================================

class TestTimestampCollection:
    """Test interval computation from tick timestamps."""

    def test_known_timestamps(self):
        """Verify interval calculation from known timestamps."""
        timestamps = [
            utc(2025, 1, 1, 10, 0, 0, ms=0),
            utc(2025, 1, 1, 10, 0, 0, ms=50),    # 50ms later
            utc(2025, 1, 1, 10, 0, 0, ms=150),   # 100ms later
            utc(2025, 1, 1, 10, 0, 1, ms=0),     # 850ms later
        ]

        intervals = make_intervals_from_timestamps(timestamps)

        assert len(intervals) == 3
        assert intervals[0] == pytest.approx(50.0)
        assert intervals[1] == pytest.approx(100.0)
        assert intervals[2] == pytest.approx(850.0)

    def test_single_timestamp_no_intervals(self):
        """Single timestamp produces no intervals."""
        timestamps = [utc(2025, 1, 1, 10, 0, 0)]
        intervals = make_intervals_from_timestamps(timestamps)
        assert len(intervals) == 0

    def test_empty_timestamps(self):
        """Empty list produces no intervals."""
        intervals = make_intervals_from_timestamps([])
        assert len(intervals) == 0
