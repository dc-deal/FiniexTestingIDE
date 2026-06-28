"""
BlocksSplit Tests
=================
Unit tests for chronological block splitting + the shared continuous-region extraction.

Tests cover: region extraction (ContinuousRegionExtractor), constrained/extended block
generation, data quality warnings, and count limiting.
"""

import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from python.framework.types.coverage_report_types import GapCategory
from python.framework.types.market_types.market_volatility_profile_types import (
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    BlocksStrategyConfig,
)
from python.framework.types.scenario_types.scenario_generator_types import GenerationStrategy
from python.scenario.generator.splitters.blocks_split import BlocksSplit
from python.scenario.generator.splitters.continuous_region_extractor import ContinuousRegionExtractor

from conftest import utc, make_gap, mock_coverage_report


# =============================================================================
# REGION EXTRACTION (ContinuousRegionExtractor)
# =============================================================================

class TestExtractContinuousRegions:
    """Tests for ContinuousRegionExtractor.extract()."""

    def test_no_gaps_single_region(self):
        """No gaps → single region spanning full data range."""
        extractor = ContinuousRegionExtractor()
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 5)
        report = mock_coverage_report(start, end, gaps=[])

        regions = extractor.extract(report)

        assert len(regions) == 1
        assert regions[0]['start'] == start
        assert regions[0]['end'] == end
        assert regions[0]['following_gap'] is None
        assert regions[0]['preceding_gap'] is None

    def test_small_gaps_ignored(self):
        """SMALL gaps don't split regions."""
        extractor = ContinuousRegionExtractor()
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 5)
        small_gap = make_gap(
            utc(2025, 10, 2, 10), utc(2025, 10, 2, 11), GapCategory.SHORT
        )
        report = mock_coverage_report(start, end, gaps=[small_gap])

        regions = extractor.extract(report)

        assert len(regions) == 1
        assert regions[0]['start'] == start
        assert regions[0]['end'] == end

    def test_weekend_gap_does_not_split_region(self):
        """WEEKEND gap is allowed — blocks span across it (professional platform behavior)."""
        extractor = ContinuousRegionExtractor()
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 8)
        weekend_gap = make_gap(
            utc(2025, 10, 3, 22), utc(2025, 10, 5, 22), GapCategory.WEEKEND
        )
        report = mock_coverage_report(start, end, gaps=[weekend_gap])

        regions = extractor.extract(report)

        assert len(regions) == 1
        assert regions[0]['start'] == start
        assert regions[0]['end'] == end
        assert regions[0]['following_gap'] is None

    def test_moderate_gap_splits_region(self):
        """MODERATE gap splits data into two regions."""
        extractor = ContinuousRegionExtractor()
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 3)
        mod_gap = make_gap(
            utc(2025, 10, 1, 20), utc(2025, 10, 2, 4), GapCategory.MODERATE
        )
        report = mock_coverage_report(start, end, gaps=[mod_gap])

        regions = extractor.extract(report)

        assert len(regions) == 2
        assert regions[0]['end'] == utc(2025, 10, 1, 20)
        assert regions[0]['preceding_gap'] is None
        assert regions[1]['start'] == utc(2025, 10, 2, 4)
        assert regions[1]['preceding_gap'] == mod_gap

    def test_large_gap_splits_region(self):
        """LARGE gap splits data."""
        extractor = ContinuousRegionExtractor()
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 5)
        large_gap = make_gap(
            utc(2025, 10, 2, 12), utc(2025, 10, 3, 8), GapCategory.LARGE
        )
        report = mock_coverage_report(start, end, gaps=[large_gap])

        regions = extractor.extract(report)

        assert len(regions) == 2

    def test_multiple_gaps_multiple_regions(self):
        """Only non-allowed gaps split regions; weekend gaps are allowed."""
        extractor = ContinuousRegionExtractor()
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 15)
        gaps = [
            make_gap(utc(2025, 10, 3, 22), utc(2025, 10, 5, 22), GapCategory.WEEKEND),
            make_gap(utc(2025, 10, 7, 10), utc(2025, 10, 7, 18), GapCategory.MODERATE),
            make_gap(utc(2025, 10, 10, 22), utc(2025, 10, 12, 22), GapCategory.WEEKEND),
        ]
        report = mock_coverage_report(start, end, gaps=gaps)

        regions = extractor.extract(report)

        # Only MODERATE gap splits — 2 WEEKEND gaps are allowed
        assert len(regions) == 2

    def test_gap_at_data_start(self):
        """Gap starting at data start → region starts after gap."""
        extractor = ContinuousRegionExtractor()
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 5)
        gap = make_gap(start, utc(2025, 10, 2), GapCategory.LARGE)
        report = mock_coverage_report(start, end, gaps=[gap])

        regions = extractor.extract(report)

        assert len(regions) == 1
        assert regions[0]['start'] == utc(2025, 10, 2)
        assert regions[0]['preceding_gap'] == gap

    def test_clip_to_range(self):
        """Regions are clipped to the requested time range."""
        extractor = ContinuousRegionExtractor()
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 10)
        report = mock_coverage_report(start, end, gaps=[])

        regions = extractor.extract(report, utc(2025, 10, 3), utc(2025, 10, 6))

        assert len(regions) == 1
        assert regions[0]['start'] == utc(2025, 10, 3)
        assert regions[0]['end'] == utc(2025, 10, 6)


# =============================================================================
# CONSTRAINED BLOCKS
# =============================================================================

class TestConstrainedBlocks:
    """Tests for block generation within a continuous region."""

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_generates_full_blocks(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Generates correct number of full blocks from continuous data."""
        splitter = BlocksSplit(blocks_config)
        # 12h data = 3 full blocks of 4h
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 12)  # 12h total
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=None)

        assert result.symbol == 'USDJPY'
        assert result.broker_type == 'mt5'
        assert result.strategy == GenerationStrategy.BLOCKS
        assert len(result.windows) == 3
        for w in result.windows:
            assert w.block_duration_hours == 4.0
            assert w.estimated_ticks == 0

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_short_last_block_above_minimum(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Last block shorter than target but ≥ min_block_hours → generated."""
        splitter = BlocksSplit(blocks_config)
        # 6h data = 1 full block (4h) + 2h remainder (≥ 1h min)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 6)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=None)

        assert len(result.windows) == 2
        assert result.windows[-1].block_duration_hours == 2.0

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_remainder_below_minimum_skipped(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Remainder < min_block_hours → skipped, not generated."""
        splitter = BlocksSplit(blocks_config)
        # 4.5h data = 1 full block (4h) + 0.5h remainder (< 1h min)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 4) + timedelta(minutes=30)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=None)

        assert len(result.windows) == 1

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_region_below_minimum_block_hours(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Region shorter than min_block_hours → no blocks generated."""
        splitter = BlocksSplit(blocks_config)
        # 0.5h data < 1h min_block_hours → no blocks
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1) + timedelta(minutes=30)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=None)

        assert len(result.windows) == 0

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_blocks_are_consecutive(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Blocks follow each other without gaps."""
        splitter = BlocksSplit(blocks_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 12)  # 12h = 3 blocks of 4h
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=None)
        windows = result.windows

        for i in range(len(windows) - 1):
            assert windows[i].end_time == windows[i + 1].start_time

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_block_index_is_zero_based_sequence(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Windows carry a 0-based sequential block_index."""
        splitter = BlocksSplit(blocks_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 12)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=None)

        assert [w.block_index for w in result.windows] == [0, 1, 2]


# =============================================================================
# COUNT LIMITING
# =============================================================================

class TestCountLimiting:
    """Tests for count_max parameter."""

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_count_max_truncates(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """count_max < generated blocks → truncates to count_max."""
        splitter = BlocksSplit(blocks_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 12)  # 3 blocks possible (12h / 4h)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=2)

        assert len(result.windows) == 2

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_count_max_above_generated(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """count_max > generated blocks → all blocks returned."""
        splitter = BlocksSplit(blocks_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 12)  # 3 blocks possible (12h / 4h)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=10)

        assert len(result.windows) == 3


# =============================================================================
# DATA QUALITY WARNINGS
# =============================================================================

class TestDataQualityWarnings:
    """Tests for data-start and post-gap warnings."""

    @patch('python.scenario.generator.splitters.blocks_split.vLog')
    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_data_start_warning(
        self, mock_dcr_cls, mock_tim_cls, mock_vlog, blocks_config: BlocksStrategyConfig
    ):
        """First block at data begin → data-start warning logged."""
        splitter = BlocksSplit(blocks_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 8)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=None)

        assert len(result.windows) == 2
        # First block starts at data begin
        assert result.windows[0].start_time == start
        # Warning about data-start was logged
        warning_calls = [str(c) for c in mock_vlog.warning.call_args_list]
        assert any('data begin' in w for w in warning_calls)

    @patch('python.scenario.generator.splitters.blocks_split.vLog')
    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_post_gap_warning(
        self, mock_dcr_cls, mock_tim_cls, mock_vlog, blocks_config: BlocksStrategyConfig
    ):
        """Block after MODERATE gap → post-gap warning logged."""
        splitter = BlocksSplit(blocks_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 2, 12)
        # MODERATE gap splits into two regions
        gap = make_gap(
            utc(2025, 10, 1, 12), utc(2025, 10, 1, 20), GapCategory.MODERATE
        )
        report = mock_coverage_report(start, end, gaps=[gap])
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=None)

        # Should have blocks from both regions
        assert len(result.windows) > 0
        # Post-gap warning for second region was logged
        warning_calls = [str(c) for c in mock_vlog.warning.call_args_list]
        assert any('MODERATE' in w and 'gap' in w for w in warning_calls)

    @patch('python.scenario.generator.splitters.blocks_split.vLog')
    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_no_warning_mid_region(
        self, mock_dcr_cls, mock_tim_cls, mock_vlog, blocks_config: BlocksStrategyConfig
    ):
        """Blocks within continuous region (not first, no preceding gap) → no post-gap warnings."""
        splitter = BlocksSplit(blocks_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 12)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=None)

        assert len(result.windows) == 3
        # Data-start warning IS expected here (single region, start == data start)
        # but NO post-gap warning (no preceding gap)
        warning_calls = [str(c) for c in mock_vlog.warning.call_args_list]
        assert not any('follows a' in w and 'gap' in w for w in warning_calls)


# =============================================================================
# NO CONTINUOUS REGIONS ERROR
# =============================================================================

class TestNoContinuousRegions:
    """Tests for edge case: no usable data."""

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_gap_covers_all_data_raises(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Gap covering entire data range → ValueError."""
        splitter = BlocksSplit(blocks_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 3)
        # Gap covers entire range
        gap = make_gap(start, end, GapCategory.LARGE)
        report = mock_coverage_report(start, end, gaps=[gap])
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        with pytest.raises(ValueError, match='No continuous data regions found'):
            splitter.split('mt5', 'USDJPY', count_max=None)


# =============================================================================
# WINDOW PROPERTIES
# =============================================================================

class TestWindowProperties:
    """Tests for correct GeneratedWindow field values."""

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_window_fields(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Blocks windows have correct default field values."""
        splitter = BlocksSplit(blocks_config)
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 10)
        report = mock_coverage_report(start, end)
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'USDJPY', count_max=1)

        assert result.symbol == 'USDJPY'
        assert result.broker_type == 'mt5'
        assert len(result.windows) == 1
        w = result.windows[0]
        assert w.estimated_ticks == 0
        assert w.regime == VolatilityRegime.MEDIUM
        assert w.atr == 0.0
        assert w.tick_density == 0.0


# =============================================================================
# START / END CLIPPING (the split now honors the requested time range)
# =============================================================================

class TestStartEndClipping:
    """BlocksSplit forwards start_time / end_time to the region extractor (clips the range)."""

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_clips_blocks_to_requested_range(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Blocks are restricted to [start_time, end_time], not the full data coverage."""
        splitter = BlocksSplit(blocks_config)
        # 24h of data available, but request only the middle 8h
        report = mock_coverage_report(utc(2025, 10, 1), utc(2025, 10, 2))
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split(
            'mt5', 'EURUSD', utc(2025, 10, 1, 8), utc(2025, 10, 1, 16))

        assert result.windows
        assert result.windows[0].start_time == utc(2025, 10, 1, 8)
        assert result.windows[-1].end_time <= utc(2025, 10, 1, 16)
        # 8h clipped range / 4h blocks → 2 blocks
        assert len(result.windows) == 2

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_no_range_spans_full_coverage(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """No start/end → blocks span the full coverage (unchanged default)."""
        splitter = BlocksSplit(blocks_config)
        report = mock_coverage_report(utc(2025, 10, 1), utc(2025, 10, 1, 8))
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'EURUSD', count_max=None)

        assert result.windows[0].start_time == utc(2025, 10, 1)
        assert result.windows[-1].end_time == utc(2025, 10, 1, 8)


# =============================================================================
# GAP-AWARE BLOCK START (boundaries snap out of market-closed windows)
# =============================================================================

class TestGapAwareBlockStart:
    """A block boundary landing in a weekend/holiday snaps to the next market open (§37)."""

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_weekend_start_snaps_to_monday(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """A region beginning on a Saturday yields a first block snapped to Monday."""
        splitter = BlocksSplit(blocks_config)
        # 2025-10-04 is a Saturday, 2025-10-06 a Monday
        report = mock_coverage_report(utc(2025, 10, 4), utc(2025, 10, 8))
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('mt5', 'EURUSD', count_max=None)

        assert result.windows
        assert result.windows[0].start_time == utc(2025, 10, 6)  # snapped to Monday 00:00
        # No window starts on a weekend day
        assert all(w.start_time.weekday() <= 4 for w in result.windows)

    @patch('python.scenario.generator.splitters.blocks_split.TickIndexManager')
    @patch('python.scenario.generator.splitters.blocks_split.DataCoverageReport')
    def test_crypto_weekend_start_not_snapped(
        self, mock_dcr_cls, mock_tim_cls, blocks_config: BlocksStrategyConfig
    ):
        """Crypto (24/7, no weekend closure) keeps a Saturday boundary — the snap is gated off."""
        splitter = BlocksSplit(blocks_config)
        # Same Saturday-start region, but a crypto broker → weekend_closure=False
        report = mock_coverage_report(utc(2025, 10, 4), utc(2025, 10, 8))
        mock_dcr_cls.return_value = report
        mock_tim_cls.return_value = MagicMock()

        result = splitter.split('kraken_spot', 'BTCUSD', count_max=None)

        assert result.windows
        # No snap: the first block keeps the Saturday region start (crypto trades weekends)
        assert result.windows[0].start_time == utc(2025, 10, 4)  # Saturday, unchanged
