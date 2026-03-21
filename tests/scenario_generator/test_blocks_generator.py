"""
BlocksGenerator Tests
======================
Unit tests for chronological block generation.

Tests cover: region extraction, constrained/extended block generation,
session filtering, data quality warnings, and count limiting.
"""

import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from python.framework.types.coverage_report_types import GapCategory
from python.framework.types.market_types.market_volatility_profile_types import (
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    GeneratorConfig,
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
        assert regions[0]['preceding_gap'] is None

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
        assert regions[0]['preceding_gap'] is None
        assert regions[1]['start'] == utc(2025, 10, 2, 4)
        assert regions[1]['preceding_gap'] == mod_gap

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
        assert regions[0]['preceding_gap'] == gap


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
        # 12h data = 3 full blocks of 4h
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 12)  # 12h total
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
        # 6h data = 1 full block (4h) + 2h remainder (≥ 1h min)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 6)
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
        # 4.5h data = 1 full block (4h) + 0.5h remainder (< 1h min)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 4) + timedelta(minutes=30)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        assert len(scenarios) == 1

    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_region_below_minimum_block_hours(
        self, mock_dcr_cls, mock_tim_cls, generator_config: GeneratorConfig
    ):
        """Region shorter than min_block_hours → no blocks generated."""
        gen = BlocksGenerator(generator_config)
        # 0.5h data < 1h min_block_hours → no blocks
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1) + timedelta(minutes=30)
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
        end = utc(2025, 10, 1, 12)  # 12h = 3 blocks of 4h
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        for i in range(len(scenarios) - 1):
            assert scenarios[i].end_time == scenarios[i + 1].start_time


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
        end = utc(2025, 10, 1, 12)  # 3 blocks possible (12h / 4h)
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
        end = utc(2025, 10, 1, 12)  # 3 blocks possible (12h / 4h)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=10)

        assert len(scenarios) == 3


# =============================================================================
# DATA QUALITY WARNINGS
# =============================================================================

class TestDataQualityWarnings:
    """Tests for data-start and post-gap warnings."""

    @patch('python.scenario.generator.blocks_generator.vLog')
    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_data_start_warning(
        self, mock_dcr_cls, mock_tim_cls, mock_vlog, generator_config: GeneratorConfig
    ):
        """First block at data begin → data-start warning logged."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 8)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        assert len(scenarios) == 2
        # First block starts at data begin
        assert scenarios[0].start_time == start
        # Warning about data-start was logged
        warning_calls = [str(c) for c in mock_vlog.warning.call_args_list]
        assert any('data begin' in w for w in warning_calls)

    @patch('python.scenario.generator.blocks_generator.vLog')
    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_post_gap_warning(
        self, mock_dcr_cls, mock_tim_cls, mock_vlog, generator_config: GeneratorConfig
    ):
        """Block after MODERATE gap → post-gap warning logged."""
        gen = BlocksGenerator(generator_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 2, 12)
        # MODERATE gap splits into two regions
        gap = make_gap(
            utc(2025, 10, 1, 12), utc(2025, 10, 1, 20), GapCategory.MODERATE
        )
        report = mock_coverage_report(start, end, gaps=[gap])
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        # Should have blocks from both regions
        assert len(scenarios) > 0
        # Post-gap warning for second region was logged
        warning_calls = [str(c) for c in mock_vlog.warning.call_args_list]
        assert any('MODERATE' in w and 'gap' in w for w in warning_calls)

    @patch('python.scenario.generator.blocks_generator.vLog')
    @patch('python.scenario.generator.blocks_generator.TickIndexManager')
    @patch('python.scenario.generator.blocks_generator.DataCoverageReport')
    def test_no_warning_mid_region(
        self, mock_dcr_cls, mock_tim_cls, mock_vlog, generator_config: GeneratorConfig
    ):
        """Blocks within continuous region (not first, no preceding gap) → no data quality warnings."""
        gen = BlocksGenerator(generator_config)
        # Data starts before our start_filter, so first region has preceding data
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 12)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        scenarios = gen.generate('mt5', 'USDJPY', block_hours=4, count_max=None)

        assert len(scenarios) == 3
        # Data-start warning IS expected here (single region, start == data start)
        # but NO post-gap warning (no preceding gap)
        warning_calls = [str(c) for c in mock_vlog.warning.call_args_list]
        assert not any('follows a' in w and 'gap' in w for w in warning_calls)


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
