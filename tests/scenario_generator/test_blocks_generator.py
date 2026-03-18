"""
BlocksGenerator Tests
======================
Unit tests for chronological block generation.

Tests cover: region extraction, constrained/extended block generation,
session filtering, warmup handling, and count limiting.
"""

import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from python.framework.types.coverage_report_types import GapCategory
from python.framework.types.market_types.market_analysis_types import (
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    GeneratorConfig,
    ScenarioCandidate,
)
from python.scenario.generator.blocks_generator import BlocksGenerator

from conftest import utc, make_gap, mock_coverage_report


# =============================================================================
# REGION EXTRACTION
# =============================================================================

class TestExtractContinuousRegions:
    """Tests for _extract_continuous_regions()."""

    def test_no_gaps_single_region(self, generator_config: GeneratorConfig):
        """No gaps → single region spanning full data range."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 5)
        report = mock_coverage_report(start, end, gaps=[])

        regions = gen._extract_continuous_regions(report)

        assert len(regions) == 1
        assert regions[0]['start'] == start
        assert regions[0]['end'] == end
        assert regions[0]['following_gap'] is None

    def test_small_gaps_ignored(self, generator_config: GeneratorConfig):
        """SMALL gaps don't split regions."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 5)
        small_gap = make_gap(
            utc(2025, 10, 2, 10), utc(2025, 10, 2, 11), GapCategory.SHORT
        )
        report = mock_coverage_report(start, end, gaps=[small_gap])

        regions = gen._extract_continuous_regions(report)

        assert len(regions) == 1
        assert regions[0]['start'] == start
        assert regions[0]['end'] == end

    def test_weekend_gap_does_not_split_region(self, generator_config: GeneratorConfig):
        """WEEKEND gap is allowed — blocks span across it (professional platform behavior)."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 8)
        weekend_gap = make_gap(
            utc(2025, 10, 3, 22), utc(2025, 10, 5, 22), GapCategory.WEEKEND
        )
        report = mock_coverage_report(start, end, gaps=[weekend_gap])

        regions = gen._extract_continuous_regions(report)

        assert len(regions) == 1
        assert regions[0]['start'] == start
        assert regions[0]['end'] == end
        assert regions[0]['following_gap'] is None

    def test_moderate_gap_splits_region(self, generator_config: GeneratorConfig):
        """MODERATE gap splits data into two regions."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 3)
        mod_gap = make_gap(
            utc(2025, 10, 1, 20), utc(2025, 10, 2, 4), GapCategory.MODERATE
        )
        report = mock_coverage_report(start, end, gaps=[mod_gap])

        regions = gen._extract_continuous_regions(report)

        assert len(regions) == 2
        assert regions[0]['end'] == utc(2025, 10, 1, 20)
        assert regions[1]['start'] == utc(2025, 10, 2, 4)

    def test_large_gap_splits_region(self, generator_config: GeneratorConfig):
        """LARGE gap splits data."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 5)
        large_gap = make_gap(
            utc(2025, 10, 2, 12), utc(2025, 10, 3, 8), GapCategory.LARGE
        )
        report = mock_coverage_report(start, end, gaps=[large_gap])

        regions = gen._extract_continuous_regions(report)

        assert len(regions) == 2

    def test_multiple_gaps_multiple_regions(self, generator_config: GeneratorConfig):
        """Only non-allowed gaps split regions; weekend gaps are allowed."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 15)
        gaps = [
            make_gap(utc(2025, 10, 3, 22), utc(2025, 10, 5, 22), GapCategory.WEEKEND),
            make_gap(utc(2025, 10, 7, 10), utc(2025, 10, 7, 18), GapCategory.MODERATE),
            make_gap(utc(2025, 10, 10, 22), utc(2025, 10, 12, 22), GapCategory.WEEKEND),
        ]
        report = mock_coverage_report(start, end, gaps=gaps)

        regions = gen._extract_continuous_regions(report)

        # Only MODERATE gap splits — 2 WEEKEND gaps are allowed
        assert len(regions) == 2

    def test_gap_at_data_start(self, generator_config: GeneratorConfig):
        """Gap starting at data start → region starts after gap."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 5)
        gap = make_gap(start, utc(2025, 10, 2), GapCategory.LARGE)
        report = mock_coverage_report(start, end, gaps=[gap])

        regions = gen._extract_continuous_regions(report)

        assert len(regions) == 1
        assert regions[0]['start'] == utc(2025, 10, 2)


# =============================================================================
# CONSTRAINED BLOCKS — NO SESSIONS
# =============================================================================

class TestConstrainedBlocksNoSessions:
    """Tests for _generate_constrained_blocks() without session filter."""

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_generates_full_blocks(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Generates correct number of full blocks from continuous data."""
        gen = BlocksGenerator(generator_config)
        # 2h warmup + 12h data = 3 blocks of 4h
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 14)  # 14h total
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        assert len(scenarios) == 3
        for s in scenarios:
            duration_h = (s.end_time - s.start_time).total_seconds() / 3600
            assert duration_h == 4.0
            assert s.symbol == 'USDJPY'
            assert s.broker_type == 'mt5'
            assert s.estimated_ticks == 0

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_short_last_block_above_minimum(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Last block shorter than target but ≥ min_block_hours → generated."""
        gen = BlocksGenerator(generator_config)
        # 2h warmup + 6h data = 1 full block (4h) + 2h remainder (≥ 1h min)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 8)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        assert len(scenarios) == 2
        last_duration = (scenarios[-1].end_time - scenarios[-1].start_time).total_seconds() / 3600
        assert last_duration == 2.0

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_remainder_below_minimum_skipped(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Remainder < min_block_hours → skipped, not generated."""
        gen = BlocksGenerator(generator_config)
        # 2h warmup + 4.5h data = 1 full block (4h) + 0.5h remainder (< 1h min)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 6) + timedelta(minutes=30)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        assert len(scenarios) == 1

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_region_too_short_for_warmup(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Region shorter than warmup → no blocks generated."""
        gen = BlocksGenerator(generator_config)
        # Only 1h of data, warmup needs 2h
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 1)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        assert len(scenarios) == 0

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_blocks_are_consecutive(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Blocks follow each other without gaps."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 14)  # 2h warmup + 12h = 3 blocks
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        for i in range(len(scenarios) - 1):
            assert scenarios[i].end_time == scenarios[i + 1].start_time


# =============================================================================
# CONSTRAINED BLOCKS — WITH SESSION FILTER
# =============================================================================

class TestConstrainedBlocksWithSessions:
    """Tests for constrained blocks with session filtering (extend=false)."""

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_session_window_filtering(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Only generates blocks within allowed session windows."""
        generator_config.blocks.extend_blocks_beyond_session = False
        gen = BlocksGenerator(generator_config)
        # 48h of data, filter to new_york (16-21 UTC)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 3)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate(
            'mt5', 'USDJPY', block_hours=4, count_max=None,
            sessions_filter=['new_york']
        )

        # All blocks must start within new_york hours (16-21 UTC)
        for s in scenarios:
            assert 16 <= s.start_time.hour < 21


# =============================================================================
# EXTENDED BLOCKS — WITH SESSION FILTER
# =============================================================================

class TestExtendedBlocksWithSessions:
    """Tests for extended blocks where sessions define START points only."""

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_blocks_start_at_session_transitions(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Extended blocks start at session transition points."""
        generator_config.blocks.extend_blocks_beyond_session = True
        gen = BlocksGenerator(generator_config)
        # 48h of data, warmup=2h, new_york starts at hour 16
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 3)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate(
            'mt5', 'USDJPY', block_hours=4, count_max=None,
            sessions_filter=['new_york']
        )

        # Blocks should start at new_york session start (hour 16)
        assert len(scenarios) > 0
        for s in scenarios:
            assert s.start_time.hour == 16

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_extended_blocks_run_full_duration(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Extended blocks run full duration past session boundary."""
        generator_config.blocks.extend_blocks_beyond_session = True
        gen = BlocksGenerator(generator_config)
        # Enough data for a full block starting at session start
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 2, 23)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate(
            'mt5', 'USDJPY', block_hours=4, count_max=None,
            sessions_filter=['new_york']
        )

        for s in scenarios:
            duration_h = (s.end_time - s.start_time).total_seconds() / 3600
            # Full blocks are exactly block_hours, short blocks are less
            assert duration_h <= 4.0
            assert duration_h >= 1.0

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_no_session_start_in_region(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """No session start found in usable region → no blocks."""
        generator_config.blocks.extend_blocks_beyond_session = True
        gen = BlocksGenerator(generator_config)
        # Region is only 3h within london session (8-11), warmup=2h,
        # so usable part is 10-11, no new_york start there
        start = utc(2025, 10, 1, 8)
        end = utc(2025, 10, 1, 11)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate(
            'mt5', 'USDJPY', block_hours=4, count_max=None,
            sessions_filter=['new_york']
        )

        assert len(scenarios) == 0


# =============================================================================
# COUNT LIMITING
# =============================================================================

class TestCountLimiting:
    """Tests for count_max parameter."""

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_count_max_truncates(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """count_max < generated blocks → truncates to count_max."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 14)  # 3 blocks possible
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=2)

        assert len(scenarios) == 2

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_count_max_above_generated(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """count_max > generated blocks → all blocks returned."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 14)  # 3 blocks possible
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=10)

        assert len(scenarios) == 3


# =============================================================================
# WARMUP HANDLING
# =============================================================================

class TestWarmupHandling:
    """Tests for warmup behavior after gaps."""

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_warmup_applied_at_region_start(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """First block starts after warmup period."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 10)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        # First block should start at warmup (2h) offset
        assert scenarios[0].start_time == start + timedelta(hours=2)

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_warmup_applied_after_gap(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Warmup reapplied in each region after a gap."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 3)
        # Weekend gap splits into two regions
        gap = make_gap(
            utc(2025, 10, 1, 12), utc(2025, 10, 2), GapCategory.WEEKEND
        )
        report = mock_coverage_report(start, end, gaps=[gap])
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        # Find blocks from second region — first block should start 2h after gap end
        region2_blocks = [s for s in scenarios if s.start_time >= utc(2025, 10, 2)]
        if region2_blocks:
            assert region2_blocks[0].start_time == utc(2025, 10, 2, 2)


# =============================================================================
# SESSION WINDOW EXTRACTION
# =============================================================================

class TestSessionWindowExtraction:
    """Tests for _extract_session_windows()."""

    def test_hours_in_allowed_session(self, generator_config: GeneratorConfig):
        """Hours within allowed session → one window."""
        gen = BlocksGenerator(generator_config)
        # new_york is 16-21 UTC
        start = utc(2025, 10, 1, 16)
        end = utc(2025, 10, 1, 21)
        allowed = {TradingSession.NEW_YORK}

        windows = gen._extract_session_windows(start, end, allowed)

        assert len(windows) == 1
        assert windows[0]['start'] == start
        assert windows[0]['end'] == end

    def test_mixed_sessions_fragmenting(self, generator_config: GeneratorConfig):
        """Range spanning multiple sessions → only allowed sessions produce windows."""
        gen = BlocksGenerator(generator_config)
        # Full day: london (8-16), new_york (16-21), transition (21-22), sydney (22-8)
        start = utc(2025, 10, 1, 8)
        end = utc(2025, 10, 2, 8)
        allowed = {TradingSession.NEW_YORK}

        windows = gen._extract_session_windows(start, end, allowed)

        # Should get one new_york window per day
        assert len(windows) >= 1
        for w in windows:
            # Window starts must be in new_york hours
            assert w['start'].hour >= 16

    def test_no_matching_hours(self, generator_config: GeneratorConfig):
        """Range with no hours in allowed session → empty."""
        gen = BlocksGenerator(generator_config)
        # Only london hours (8-12), filter for new_york
        start = utc(2025, 10, 1, 8)
        end = utc(2025, 10, 1, 12)
        allowed = {TradingSession.NEW_YORK}

        windows = gen._extract_session_windows(start, end, allowed)

        assert len(windows) == 0


# =============================================================================
# SESSION START POINT EXTRACTION
# =============================================================================

class TestSessionStartPoints:
    """Tests for _extract_session_start_points()."""

    def test_detects_session_transition(self, generator_config: GeneratorConfig):
        """Detects transition from non-allowed to allowed session."""
        gen = BlocksGenerator(generator_config)
        # Span from london into new_york
        start = utc(2025, 10, 1, 14)
        end = utc(2025, 10, 1, 20)
        allowed = {TradingSession.NEW_YORK}

        start_points = gen._extract_session_start_points(start, end, allowed)

        assert len(start_points) == 1
        assert start_points[0].hour == 16

    def test_no_transition_in_range(self, generator_config: GeneratorConfig):
        """No session transition within range → empty."""
        gen = BlocksGenerator(generator_config)
        # Only london hours
        start = utc(2025, 10, 1, 9)
        end = utc(2025, 10, 1, 12)
        allowed = {TradingSession.NEW_YORK}

        start_points = gen._extract_session_start_points(start, end, allowed)

        assert len(start_points) == 0


# =============================================================================
# NO CONTINUOUS REGIONS ERROR
# =============================================================================

class TestNoContinuousRegions:
    """Tests for edge case: no usable data."""

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_gap_covers_all_data_raises(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Gap covering entire data range → ValueError."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 3)
        # Gap covers entire range
        gap = make_gap(start, end, GapCategory.LARGE)
        report = mock_coverage_report(start, end, gaps=[gap])
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        with pytest.raises(ValueError, match='No continuous data regions found'):
            gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)


# =============================================================================
# SCENARIO CANDIDATE PROPERTIES
# =============================================================================

class TestScenarioCandidateProperties:
    """Tests for correct ScenarioCandidate field values."""

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_candidate_fields(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Blocks candidates have correct default field values."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 10)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=1)

        assert len(scenarios) == 1
        s = scenarios[0]
        assert s.symbol == 'USDJPY'
        assert s.broker_type == 'mt5'
        assert s.estimated_ticks == 0
        assert s.regime == VolatilityRegime.MEDIUM
        assert s.atr == 0.0
        assert s.real_bar_ratio == 1.0
