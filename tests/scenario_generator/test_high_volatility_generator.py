"""
HighVolatilityGenerator Tests
===============================
Unit tests for high-volatility scenario generation.

Tests cover: centering, warmup validation, quality checks,
gap detection, overlap prevention, and full generation flow.
"""

import pytest
from datetime import timedelta
from typing import List

from python.framework.types.market_types.market_analysis_types import (
    PeriodAnalysis,
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    GeneratorConfig,
)
from python.scenario.generator.high_volatility_generator import HighVolatilityGenerator

from conftest import utc, make_period, make_continuous_periods, mock_analyzer


# =============================================================================
# SCENARIO CENTERING
# =============================================================================

class TestScenarioCentering:
    """Tests for scenario centering around high-volatility periods."""

    def test_centered_on_period(self, generator_config: GeneratorConfig):
        """Scenario is centered on high-vol period with hour alignment."""
        # Period at 10:00-11:00, center at 10:30
        # block_hours=4 → ±2h → 08:00-12:00 (after floor alignment)
        high_vol = make_period(
            utc(2025, 10, 5, 10),
            regime=VolatilityRegime.HIGH,
            tick_count=5000
        )
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=1)

        assert len(scenarios) == 1
        s = scenarios[0]
        # Center at 10:30 - 2h = 08:30 → floored to 08:00
        assert s.start_time == utc(2025, 10, 5, 8)
        assert s.end_time == utc(2025, 10, 5, 12)

    def test_hour_alignment_no_sub_hour_drift(self, generator_config: GeneratorConfig):
        """Scenario start is always on full hour boundary."""
        high_vol = make_period(
            utc(2025, 10, 5, 15),
            regime=VolatilityRegime.VERY_HIGH,
            tick_count=8000
        )
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=1)

        assert len(scenarios) == 1
        assert scenarios[0].start_time.minute == 0
        assert scenarios[0].start_time.second == 0


# =============================================================================
# VALIDATION CHECKS
# =============================================================================

class TestInsufficientWarmup:
    """Tests for warmup validation (Check 1)."""

    def test_skip_insufficient_warmup(self, generator_config: GeneratorConfig):
        """Period too close to data start for warmup → skipped."""
        # Data starts at hour 0, period at hour 1
        # Scenario centered at 1:30, start=23:30→23:00 (prev day)
        # Warmup = 23:00 - 2h = 21:00 (prev day) — but data starts at 00:00
        high_vol = make_period(
            utc(2025, 10, 5, 1),
            regime=VolatilityRegime.HIGH,
            tick_count=5000
        )
        # Data only from hour 0 — warmup needs data before that
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        # This should skip the period due to insufficient warmup
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=1)

        assert len(scenarios) == 0


class TestLowQuality:
    """Tests for real bar ratio validation (Check 2)."""

    def test_skip_low_real_bar_ratio(self, generator_config: GeneratorConfig):
        """Period with low real bar ratio → skipped."""
        # real_bar_count=2, bar_count=12 → ratio=0.17 < 0.5 threshold
        high_vol = make_period(
            utc(2025, 10, 5, 12),
            regime=VolatilityRegime.HIGH,
            tick_count=5000,
            bar_count=12,
            real_bar_count=2
        )
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=1)

        assert len(scenarios) == 0

    def test_accept_good_real_bar_ratio(self, generator_config: GeneratorConfig):
        """Period with sufficient real bar ratio → accepted."""
        high_vol = make_period(
            utc(2025, 10, 5, 12),
            regime=VolatilityRegime.HIGH,
            tick_count=5000,
            bar_count=12,
            real_bar_count=10
        )
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=1)

        assert len(scenarios) == 1


class TestGapDetection:
    """Tests for gap-in-window validation (Check 3)."""

    def test_skip_gap_in_window(self, generator_config: GeneratorConfig):
        """Missing period in scenario window → skipped."""
        high_vol = make_period(
            utc(2025, 10, 5, 12),
            regime=VolatilityRegime.HIGH,
            tick_count=5000
        )
        # Create periods with a gap (missing hour 11)
        periods = make_continuous_periods(utc(2025, 10, 5), hours=11)
        # Skip hour 11, continue from hour 12
        periods += make_continuous_periods(utc(2025, 10, 5, 12), hours=12)
        analyzer = mock_analyzer([high_vol], periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=1)

        assert len(scenarios) == 0

    def test_accept_continuous_window(self, generator_config: GeneratorConfig):
        """No gaps in scenario window → accepted."""
        high_vol = make_period(
            utc(2025, 10, 5, 12),
            regime=VolatilityRegime.HIGH,
            tick_count=5000
        )
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=1)

        assert len(scenarios) == 1


class TestOverlapDetection:
    """Tests for overlap prevention (Check 4)."""

    def test_skip_overlapping_scenario(self, generator_config: GeneratorConfig):
        """Second scenario overlapping first → skipped."""
        # Two adjacent high-vol periods that would create overlapping scenarios
        high_vol_1 = make_period(
            utc(2025, 10, 5, 12),
            regime=VolatilityRegime.HIGH,
            tick_count=6000
        )
        high_vol_2 = make_period(
            utc(2025, 10, 5, 13),
            regime=VolatilityRegime.HIGH,
            tick_count=5000
        )
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol_1, high_vol_2], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=2)

        # Second should be skipped due to overlap with first
        assert len(scenarios) == 1

    def test_non_overlapping_accepted(self, generator_config: GeneratorConfig):
        """Well-separated periods → both accepted."""
        high_vol_1 = make_period(
            utc(2025, 10, 5, 6),
            regime=VolatilityRegime.HIGH,
            tick_count=5000
        )
        high_vol_2 = make_period(
            utc(2025, 10, 5, 18),
            regime=VolatilityRegime.HIGH,
            tick_count=5000
        )
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol_1, high_vol_2], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=2)

        assert len(scenarios) == 2


# =============================================================================
# _has_overlap() UNIT TESTS
# =============================================================================

class TestHasOverlap:
    """Tests for _has_overlap() helper method."""

    def test_no_overlap_before(self, generator_config: GeneratorConfig):
        """New range entirely before used range → no overlap."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)

        result = gen._has_overlap(
            utc(2025, 10, 1), utc(2025, 10, 2),
            [(utc(2025, 10, 3), utc(2025, 10, 4))]
        )

        assert result is False

    def test_no_overlap_after(self, generator_config: GeneratorConfig):
        """New range entirely after used range → no overlap."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)

        result = gen._has_overlap(
            utc(2025, 10, 5), utc(2025, 10, 6),
            [(utc(2025, 10, 3), utc(2025, 10, 4))]
        )

        assert result is False

    def test_adjacent_ranges_no_overlap(self, generator_config: GeneratorConfig):
        """Touching ranges (end == start) → no overlap."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)

        result = gen._has_overlap(
            utc(2025, 10, 2), utc(2025, 10, 3),
            [(utc(2025, 10, 1), utc(2025, 10, 2))]
        )

        assert result is False

    def test_partial_overlap(self, generator_config: GeneratorConfig):
        """Partial overlap → detected."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)

        result = gen._has_overlap(
            utc(2025, 10, 1, 12), utc(2025, 10, 2, 12),
            [(utc(2025, 10, 1), utc(2025, 10, 2))]
        )

        assert result is True

    def test_no_used_ranges(self, generator_config: GeneratorConfig):
        """Empty used_ranges → no overlap."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)

        result = gen._has_overlap(
            utc(2025, 10, 1), utc(2025, 10, 2), []
        )

        assert result is False


# =============================================================================
# _check_gap_in_window() UNIT TESTS
# =============================================================================

class TestCheckGapInWindow:
    """Tests for _check_gap_in_window() helper method."""

    def test_continuous_periods_no_gap(self, generator_config: GeneratorConfig):
        """Continuous periods covering full window → None."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)
        periods = make_continuous_periods(utc(2025, 10, 5, 8), hours=6)

        result = gen._check_gap_in_window(
            utc(2025, 10, 5, 8), utc(2025, 10, 5, 14), periods
        )

        assert result is None

    def test_gap_between_periods(self, generator_config: GeneratorConfig):
        """Missing period between two consecutive → gap detected."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)
        # Hours 8-10, then skip 10, then 11-12
        periods = make_continuous_periods(utc(2025, 10, 5, 8), hours=2)
        periods += make_continuous_periods(utc(2025, 10, 5, 11), hours=1)

        result = gen._check_gap_in_window(
            utc(2025, 10, 5, 8), utc(2025, 10, 5, 12), periods
        )

        assert result is not None
        assert 'Gap between' in result

    def test_gap_at_window_start(self, generator_config: GeneratorConfig):
        """First period starts after window start → gap at start."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)
        periods = make_continuous_periods(utc(2025, 10, 5, 10), hours=4)

        result = gen._check_gap_in_window(
            utc(2025, 10, 5, 8), utc(2025, 10, 5, 14), periods
        )

        assert result is not None
        assert 'window start' in result

    def test_gap_at_window_end(self, generator_config: GeneratorConfig):
        """Last period ends before window end → gap at end."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)
        periods = make_continuous_periods(utc(2025, 10, 5, 8), hours=4)

        result = gen._check_gap_in_window(
            utc(2025, 10, 5, 8), utc(2025, 10, 5, 16), periods
        )

        assert result is not None
        assert 'window end' in result

    def test_no_periods_in_window(self, generator_config: GeneratorConfig):
        """No periods in window → error message."""
        analyzer = mock_analyzer([], [])
        gen = HighVolatilityGenerator(generator_config, analyzer)
        # Periods outside window
        periods = make_continuous_periods(utc(2025, 10, 5, 20), hours=4)

        result = gen._check_gap_in_window(
            utc(2025, 10, 5, 8), utc(2025, 10, 5, 14), periods
        )

        assert result is not None
        assert 'No data coverage' in result


# =============================================================================
# FULL GENERATION FLOW
# =============================================================================

class TestFullGenerationFlow:
    """Tests for complete generate() flow."""

    def test_no_high_vol_periods_raises(self, generator_config: GeneratorConfig):
        """No HIGH/VERY_HIGH periods → ValueError."""
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)

        with pytest.raises(ValueError, match='No HIGH/VERY_HIGH'):
            gen.generate('mt5', 'USDJPY', block_hours=4, count=3)

    def test_fewer_valid_than_requested(self, generator_config: GeneratorConfig):
        """Only 1 valid period but 3 requested → returns 1."""
        high_vol = make_period(
            utc(2025, 10, 5, 12),
            regime=VolatilityRegime.HIGH,
            tick_count=5000
        )
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=3)

        assert len(scenarios) == 1

    def test_candidate_fields(self, generator_config: GeneratorConfig):
        """Generated candidate has correct field values from period."""
        high_vol = make_period(
            utc(2025, 10, 5, 12),
            regime=VolatilityRegime.VERY_HIGH,
            session=TradingSession.LONDON,
            tick_count=8000
        )
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer([high_vol], all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=1)

        assert len(scenarios) == 1
        s = scenarios[0]
        assert s.symbol == 'USDJPY'
        assert s.broker_type == 'mt5'
        assert s.regime == VolatilityRegime.VERY_HIGH
        assert s.session == TradingSession.LONDON
        assert s.estimated_ticks == 0
        assert s.atr == 0.5

    def test_stops_at_count_limit(self, generator_config: GeneratorConfig):
        """Stops generating after reaching count limit."""
        periods_data = []
        for h in [6, 12, 18]:
            periods_data.append(make_period(
                utc(2025, 10, 5, h),
                regime=VolatilityRegime.HIGH,
                tick_count=5000
            ))
        all_periods = make_continuous_periods(utc(2025, 10, 5), hours=24)
        analyzer = mock_analyzer(periods_data, all_periods)

        gen = HighVolatilityGenerator(generator_config, analyzer)
        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count=2)

        assert len(scenarios) == 2
