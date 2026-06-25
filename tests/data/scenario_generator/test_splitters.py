"""
Splitter Layer Tests
====================
Unit tests for the splitter abstraction: factory resolution, the walk-forward extension
point, and the profile-driven splitters (volatility_split / continuous) with mocked caches.
"""

import pytest
from unittest.mock import MagicMock, patch

from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    BlocksStrategyConfig,
    GenerationStrategy,
    ProfileStrategyConfig,
)
from python.scenario.generator.splitters.abstract_splitter import AbstractSplitter
from python.scenario.generator.splitters.blocks_split import BlocksSplit
from python.scenario.generator.splitters.continuous_split import ContinuousSplit
from python.scenario.generator.splitters.splitter_factory import SplitterFactory
from python.scenario.generator.splitters.volatility_split import VolatilitySplit
from python.scenario.generator.splitters.walk_forward_split import WalkForwardSplit

from conftest import utc, make_continuous_periods, mock_coverage_report

_PROFILE_CACHE_PATH = 'python.scenario.generator.splitters.abstract_profile_splitter'


# =============================================================================
# SPLITTER FACTORY
# =============================================================================

class TestSplitterFactory:
    """Tests for strategy → splitter resolution."""

    def test_resolves_all_strategies(self):
        """Every GenerationStrategy resolves to its splitter class."""
        factory = SplitterFactory()
        expected = {
            GenerationStrategy.BLOCKS: BlocksSplit,
            GenerationStrategy.VOLATILITY_SPLIT: VolatilitySplit,
            GenerationStrategy.CONTINUOUS: ContinuousSplit,
            GenerationStrategy.WALK_FORWARD: WalkForwardSplit,
        }
        for strategy, cls in expected.items():
            config = (BlocksStrategyConfig() if strategy == GenerationStrategy.BLOCKS
                      else ProfileStrategyConfig())
            splitter = factory.create_splitter(strategy, config)
            assert isinstance(splitter, cls)
            assert isinstance(splitter, AbstractSplitter)

    def test_every_strategy_is_registered(self):
        """No GenerationStrategy member is missing from the registry."""
        factory = SplitterFactory()
        for strategy in GenerationStrategy:
            config = (BlocksStrategyConfig() if strategy == GenerationStrategy.BLOCKS
                      else ProfileStrategyConfig())
            # Must not raise
            factory.create_splitter(strategy, config)


# =============================================================================
# WALK-FORWARD (extension point stub)
# =============================================================================

class TestWalkForwardSplit:
    """The walk-forward splitter is a registered structural stub (#32 Phase 4)."""

    def test_split_raises_not_implemented(self):
        """split() raises NotImplementedError pointing at the future work."""
        splitter = WalkForwardSplit(ProfileStrategyConfig())
        with pytest.raises(NotImplementedError, match='WalkForwardSplit'):
            splitter.split('mt5', 'EURUSD', utc(2025, 10, 1), utc(2025, 10, 5))


# =============================================================================
# PROFILE SPLITTERS (mocked caches)
# =============================================================================

class TestContinuousSplit:
    """Tests for the continuous (one-window-per-region) splitter."""

    @patch(f'{_PROFILE_CACHE_PATH}.DiscoveryCacheManager')
    @patch(f'{_PROFILE_CACHE_PATH}.VolatilityProfileAnalyzerCache')
    @patch(f'{_PROFILE_CACHE_PATH}.DataCoverageReportCache')
    def test_single_region_one_window(self, mock_cov_cls, mock_vol_cls, mock_disc_cls):
        """A gap-free region yields exactly one window with period-derived metadata."""
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 6)
        mock_cov_cls.return_value.get_report.return_value = mock_coverage_report(start, end)
        vol_profile = MagicMock()
        vol_profile.periods = make_continuous_periods(
            start, 6, regime=VolatilityRegime.HIGH, session=TradingSession.LONDON)
        mock_vol_cls.return_value.get_profile.return_value = vol_profile
        mock_disc_cls.return_value.get_fingerprints.return_value = {}

        ws = ContinuousSplit(ProfileStrategyConfig()).split('mt5', 'EURUSD', start, end)

        assert ws.strategy == GenerationStrategy.CONTINUOUS
        assert ws.mode == 'continuous'
        assert ws.block_count == 1
        assert ws.windows[0].split_reason == 'continuous_region'
        assert ws.windows[0].regime == VolatilityRegime.HIGH

    @patch(f'{_PROFILE_CACHE_PATH}.DiscoveryCacheManager')
    @patch(f'{_PROFILE_CACHE_PATH}.VolatilityProfileAnalyzerCache')
    @patch(f'{_PROFILE_CACHE_PATH}.DataCoverageReportCache')
    def test_requires_time_range(self, mock_cov_cls, mock_vol_cls, mock_disc_cls):
        """Profile splitters require start/end."""
        with pytest.raises(ValueError, match='require start_time and end_time'):
            ContinuousSplit(ProfileStrategyConfig()).split('mt5', 'EURUSD')


class TestVolatilitySplit:
    """Tests for the ATR-minima splitter."""

    @patch(f'{_PROFILE_CACHE_PATH}.DiscoveryCacheManager')
    @patch(f'{_PROFILE_CACHE_PATH}.VolatilityProfileAnalyzerCache')
    @patch(f'{_PROFILE_CACHE_PATH}.DataCoverageReportCache')
    def test_region_within_max_single_window(self, mock_cov_cls, mock_vol_cls, mock_disc_cls):
        """A region within max_block_hours produces a single (single_region) window."""
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 6)  # 6h ≤ max_block_hours=24
        mock_cov_cls.return_value.get_report.return_value = mock_coverage_report(start, end)
        vol_profile = MagicMock()
        vol_profile.periods = make_continuous_periods(start, 6)
        mock_vol_cls.return_value.get_profile.return_value = vol_profile
        mock_disc_cls.return_value.get_fingerprints.return_value = {}

        ws = VolatilitySplit(ProfileStrategyConfig()).split('mt5', 'EURUSD', start, end)

        assert ws.strategy == GenerationStrategy.VOLATILITY_SPLIT
        assert ws.mode == 'volatility_split'
        assert ws.block_count == 1
        assert ws.windows[0].split_reason == 'single_region'

    @patch(f'{_PROFILE_CACHE_PATH}.DiscoveryCacheManager')
    @patch(f'{_PROFILE_CACHE_PATH}.VolatilityProfileAnalyzerCache')
    @patch(f'{_PROFILE_CACHE_PATH}.DataCoverageReportCache')
    def test_region_exceeding_max_splits(self, mock_cov_cls, mock_vol_cls, mock_disc_cls):
        """A region longer than max_block_hours is split into multiple windows."""
        start = utc(2025, 10, 1)
        end = utc(2025, 10, 1, 8)  # 8h > max_block_hours=3
        mock_cov_cls.return_value.get_report.return_value = mock_coverage_report(start, end)
        vol_profile = MagicMock()
        vol_profile.periods = make_continuous_periods(start, 8)
        mock_vol_cls.return_value.get_profile.return_value = vol_profile
        mock_disc_cls.return_value.get_fingerprints.return_value = {}

        config = ProfileStrategyConfig(
            min_block_hours=1, max_block_hours=3, atr_percentile_threshold=50,
            split_algorithm='atr_minima')
        ws = VolatilitySplit(config).split('mt5', 'EURUSD', start, end)

        assert ws.block_count >= 2
        # No window exceeds the max block size
        assert all(w.block_duration_hours <= 3.0 + 1e-9 for w in ws.windows)
