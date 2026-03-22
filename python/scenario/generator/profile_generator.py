"""
Profile Generator
==================
Generates pre-computed, immutable block profiles using ATR-minima splitting.

Splits a time range into blocks at volatility minima (low-ATR periods),
producing a GeneratorProfile artifact with full metadata per block.

Supports two modes:
- volatility_split: ATR-minima-based splitting (default)
- continuous: One block per continuous data region (no splitting)
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from python.configuration.app_config_manager import AppConfigManager
from python.framework.discoveries.data_coverage.data_coverage_report_cache import DataCoverageReportCache
from python.framework.discoveries.discovery_cache_manager import DiscoveryCacheManager
from python.framework.discoveries.volatility_profile_analyzer.volatility_profile_analyzer_cache import VolatilityProfileAnalyzerCache
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityPeriod,
    VolatilityRegime,
)
from python.framework.types.scenario_types.generator_profile_types import (
    GeneratorProfile,
    ProfileBlock,
    ProfileMetadata,
    ProfileSplitConfig,
)
from python.framework.types.scenario_types.scenario_generator_types import ProfileStrategyConfig
from python.framework.utils.market_calendar import GapCategory
from python.scenario.generator.blocks_generator import BlocksGenerator

vLog = get_global_logger()


class ProfileGenerator:
    """
    Generates generator profiles with ATR-minima-based block splitting.

    Uses volatility periods from VolatilityProfileAnalyzer to find
    optimal split points at low-ATR periods, minimizing trade disruption.
    """

    def __init__(
        self,
        config: ProfileStrategyConfig,
        logger: AbstractLogger = None
    ):
        """
        Initialize profile generator.

        Args:
            config: Profile strategy configuration
            logger: Logger instance (falls back to global logger)
        """
        self._config = config
        self._logger = logger or vLog
        self._coverage_cache = DataCoverageReportCache(logger=self._logger)
        self._volatility_cache = VolatilityProfileAnalyzerCache(logger=self._logger)
        self._discovery_manager = DiscoveryCacheManager(logger=self._logger)

    def generate(
        self,
        broker_type: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        mode: str = 'volatility_split'
    ) -> GeneratorProfile:
        """
        Generate a profile for the given symbol and time range.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            start_time: Profile start time (UTC)
            end_time: Profile end time (UTC)
            mode: Generation mode ('volatility_split' or 'continuous')

        Returns:
            GeneratorProfile with blocks and metadata
        """
        self._logger.info(
            f"Generating profile: {broker_type}/{symbol} "
            f"{start_time.strftime('%Y-%m-%d')} → {end_time.strftime('%Y-%m-%d')} "
            f"[{mode}]"
        )

        # Get continuous data regions (gap-aware)
        regions = self._extract_regions(broker_type, symbol, start_time, end_time)

        if not regions:
            raise ValueError(
                f"No continuous data regions found for {broker_type}/{symbol} "
                f"in [{start_time} → {end_time}]"
            )

        # Get volatility periods for the time range
        vol_profile = self._volatility_cache.get_profile(broker_type, symbol)
        if vol_profile is None:
            raise ValueError(
                f"No volatility profile available for {broker_type}/{symbol}. "
                f"Run discovery cache rebuild first."
            )

        # Filter periods to requested time range
        periods = [
            p for p in vol_profile.periods
            if p.end_time > start_time and p.start_time < end_time
        ]

        if not periods:
            raise ValueError(
                f"No volatility periods found for {broker_type}/{symbol} "
                f"in [{start_time} → {end_time}]"
            )

        # Generate blocks based on mode
        if mode == 'continuous':
            blocks = self._generate_continuous_blocks(regions, periods)
        else:
            blocks = self._generate_volatility_split_blocks(regions, periods)

        # Get discovery fingerprints
        fingerprints = self._discovery_manager.get_fingerprints(broker_type, symbol)
        clean_fingerprints = {k: v for k, v in fingerprints.items() if v is not None}

        # Calculate total coverage
        total_hours = sum(b.block_duration_hours for b in blocks)

        # Build split config
        split_config = ProfileSplitConfig(
            min_block_hours=self._config.min_block_hours,
            max_block_hours=self._config.max_block_hours,
            atr_percentile_threshold=self._config.atr_percentile_threshold,
            split_algorithm=self._config.split_algorithm,
        )

        # Build metadata
        metadata = ProfileMetadata(
            symbol=symbol,
            broker_type=broker_type,
            generator_mode=mode,
            generated_at=datetime.now(timezone.utc),
            total_coverage_hours=total_hours,
            block_count=len(blocks),
            discovery_fingerprints=clean_fingerprints,
            split_config=split_config,
        )

        profile = GeneratorProfile(
            profile_meta=metadata,
            blocks=blocks,
        )

        self._print_generation_summary(profile, mode)

        return profile

    # =========================================================================
    # CONTINUOUS MODE
    # =========================================================================

    def _generate_continuous_blocks(
        self,
        regions: List[Dict],
        periods: List[VolatilityPeriod]
    ) -> List[ProfileBlock]:
        """
        Generate one block per continuous data region (no splitting).

        Args:
            regions: Continuous data regions
            periods: Volatility periods for metadata

        Returns:
            List of ProfileBlock (one per region)
        """
        blocks = []

        for i, region in enumerate(regions):
            region_start = region['start']
            region_end = region['end']
            duration_hours = (region_end - region_start).total_seconds() / 3600

            # Find representative period for metadata
            region_periods = [
                p for p in periods
                if p.end_time > region_start and p.start_time < region_end
            ]

            atr_at_split = 0.0
            regime = VolatilityRegime.MEDIUM
            session = TradingSession.TRANSITION

            if region_periods:
                avg_atr = sum(p.atr for p in region_periods) / len(region_periods)
                atr_at_split = avg_atr
                # Use median regime
                regime_counts: Dict[VolatilityRegime, int] = {}
                for p in region_periods:
                    regime_counts[p.regime] = regime_counts.get(p.regime, 0) + 1
                regime = max(regime_counts, key=regime_counts.get)
                session = region_periods[0].session

            estimated_ticks = sum(p.tick_count for p in region_periods)

            # Distance to next block
            distance = None
            if i < len(regions) - 1:
                next_start = regions[i + 1]['start']
                distance = (next_start - region_end).total_seconds() / 3600

            blocks.append(ProfileBlock(
                block_index=i,
                start_time=region_start,
                end_time=region_end,
                block_duration_hours=round(duration_hours, 2),
                split_reason='continuous_region',
                atr_at_split=round(atr_at_split, 6),
                regime_at_split=regime,
                session=session,
                estimated_ticks=estimated_ticks,
                distance_to_next_block_hours=round(distance, 2) if distance is not None else None,
            ))

        return blocks

    # =========================================================================
    # VOLATILITY SPLIT MODE (ATR-MINIMA)
    # =========================================================================

    def _generate_volatility_split_blocks(
        self,
        regions: List[Dict],
        periods: List[VolatilityPeriod]
    ) -> List[ProfileBlock]:
        """
        Generate blocks by splitting at ATR minima within continuous regions.

        Algorithm per region:
        1. Collect ATR values from periods within the region
        2. Calculate percentile threshold (P_n)
        3. Find candidate split points where atr <= threshold
        4. Greedy selection respecting min/max block hours
        5. If no candidate within max_block_hours: force split at best ATR

        Args:
            regions: Continuous data regions
            periods: Volatility periods

        Returns:
            List of ProfileBlock
        """
        all_blocks = []
        block_index = 0

        for region_idx, region in enumerate(regions):
            region_start = region['start']
            region_end = region['end']

            # Filter periods to this region
            region_periods = [
                p for p in periods
                if p.end_time > region_start and p.start_time < region_end
            ]

            if not region_periods:
                # No volatility data — single block for entire region
                self._logger.warning(
                    f"No volatility periods for region "
                    f"{region_start.strftime('%Y-%m-%d %H:%M')} → "
                    f"{region_end.strftime('%Y-%m-%d %H:%M')}, creating single block"
                )
                all_blocks.append(self._create_block(
                    block_index, region_start, region_end,
                    'no_volatility_data', 0.0, VolatilityRegime.MEDIUM,
                    TradingSession.TRANSITION, 0
                ))
                block_index += 1
                continue

            # Sort periods chronologically
            region_periods.sort(key=lambda p: p.start_time)

            # Calculate ATR threshold (percentile-based)
            atr_values = [p.atr for p in region_periods]
            threshold = float(np.percentile(atr_values, self._config.atr_percentile_threshold))

            # Find split points
            split_points = self._find_split_points(
                region_start, region_end, region_periods, threshold
            )

            # Build blocks from split points
            region_blocks = self._build_blocks_from_splits(
                block_index, region_start, region_end,
                split_points, region_periods
            )

            all_blocks.extend(region_blocks)
            block_index += len(region_blocks)

        # Calculate distance_to_next_block_hours
        for i in range(len(all_blocks) - 1):
            distance = (all_blocks[i + 1].start_time - all_blocks[i].end_time).total_seconds() / 3600
            all_blocks[i].distance_to_next_block_hours = round(distance, 2)

        return all_blocks

    def _find_split_points(
        self,
        region_start: datetime,
        region_end: datetime,
        periods: List[VolatilityPeriod],
        atr_threshold: float
    ) -> List[datetime]:
        """
        Find optimal split points using greedy ATR-minima selection.

        Args:
            region_start: Region start time
            region_end: Region end time
            periods: Volatility periods (sorted chronologically)
            atr_threshold: ATR percentile threshold

        Returns:
            List of split point datetimes (sorted)
        """
        min_hours = self._config.min_block_hours
        max_hours = self._config.max_block_hours

        # Identify candidate periods (ATR <= threshold)
        candidates = [p for p in periods if p.atr <= atr_threshold]

        # Sort candidates by ATR (prefer lowest ATR for splitting)
        candidates.sort(key=lambda p: p.atr)

        split_points = []
        current_block_start = region_start

        # Greedy: iterate chronologically, picking split points
        # We need to work chronologically, so build a set of candidate times
        candidate_times = []
        for c in candidates:
            # Split at the start of the low-ATR period
            split_time = c.start_time
            hours_from_block_start = (split_time - current_block_start).total_seconds() / 3600

            if hours_from_block_start >= min_hours:
                candidate_times.append((split_time, c.atr, c))

        # Sort by time for greedy selection
        candidate_times.sort(key=lambda x: x[0])

        current_block_start = region_start

        for split_time, atr, period in candidate_times:
            hours_from_start = (split_time - current_block_start).total_seconds() / 3600
            hours_remaining = (region_end - split_time).total_seconds() / 3600

            # Skip if block would be too short
            if hours_from_start < min_hours:
                continue

            # Skip if remaining time after split is too short for another block
            if hours_remaining < min_hours:
                continue

            split_points.append(split_time)
            current_block_start = split_time

        # Check for forced splits: if any block exceeds max_block_hours
        split_points = self._enforce_max_block_hours(
            region_start, region_end, split_points, periods
        )

        return sorted(set(split_points))

    def _enforce_max_block_hours(
        self,
        region_start: datetime,
        region_end: datetime,
        split_points: List[datetime],
        periods: List[VolatilityPeriod]
    ) -> List[datetime]:
        """
        Ensure no block exceeds max_block_hours by inserting forced splits.

        Args:
            region_start: Region start
            region_end: Region end
            split_points: Current split points
            periods: Available volatility periods

        Returns:
            Updated split points with forced splits where needed
        """
        max_hours = self._config.max_block_hours
        min_hours = self._config.min_block_hours

        # Build block boundaries
        boundaries = [region_start] + sorted(split_points) + [region_end]
        result_splits = list(split_points)

        for i in range(len(boundaries) - 1):
            block_start = boundaries[i]
            block_end = boundaries[i + 1]
            block_hours = (block_end - block_start).total_seconds() / 3600

            if block_hours <= max_hours:
                continue

            # Block too long — force split at best available ATR
            block_periods = [
                p for p in periods
                if p.start_time > block_start and p.end_time < block_end
            ]

            if not block_periods:
                continue

            # Find periods that satisfy min_block_hours constraint
            valid_periods = []
            for p in block_periods:
                hours_from_start = (p.start_time - block_start).total_seconds() / 3600
                hours_to_end = (block_end - p.start_time).total_seconds() / 3600
                if hours_from_start >= min_hours and hours_to_end >= min_hours:
                    valid_periods.append(p)

            if valid_periods:
                # Pick period with lowest ATR
                best = min(valid_periods, key=lambda p: p.atr)
                self._logger.warning(
                    f"⚠️ Forced split at {best.start_time.strftime('%Y-%m-%d %H:%M')} "
                    f"(ATR={best.atr:.4f}) — block exceeded {max_hours}h"
                )
                result_splits.append(best.start_time)
            else:
                # Last resort: split at midpoint
                midpoint = block_start + (block_end - block_start) / 2
                self._logger.warning(
                    f"⚠️ Forced split at midpoint {midpoint.strftime('%Y-%m-%d %H:%M')} "
                    f"— no valid ATR candidate within {max_hours}h block"
                )
                result_splits.append(midpoint)

        return result_splits

    def _build_blocks_from_splits(
        self,
        start_index: int,
        region_start: datetime,
        region_end: datetime,
        split_points: List[datetime],
        periods: List[VolatilityPeriod]
    ) -> List[ProfileBlock]:
        """
        Build ProfileBlock list from split points.

        Args:
            start_index: Starting block index
            region_start: Region start time
            region_end: Region end time
            split_points: Sorted split point times
            periods: Volatility periods for metadata

        Returns:
            List of ProfileBlock
        """
        boundaries = [region_start] + sorted(split_points) + [region_end]
        blocks = []

        for i in range(len(boundaries) - 1):
            block_start = boundaries[i]
            block_end = boundaries[i + 1]

            # Determine split reason
            if i == 0 and not split_points:
                split_reason = 'single_region'
            elif i == 0:
                split_reason = 'region_start'
            elif boundaries[i] in split_points:
                split_reason = 'atr_minima'
            else:
                split_reason = 'forced'

            # Get metadata from periods in this block
            block_periods = [
                p for p in periods
                if p.end_time > block_start and p.start_time < block_end
            ]

            atr_at_split = 0.0
            regime = VolatilityRegime.MEDIUM
            session = TradingSession.TRANSITION
            estimated_ticks = 0

            if block_periods:
                # ATR at the split point (first period of this block)
                atr_at_split = block_periods[0].atr
                regime = block_periods[0].regime
                session = block_periods[0].session
                estimated_ticks = sum(p.tick_count for p in block_periods)

            blocks.append(self._create_block(
                start_index + i, block_start, block_end,
                split_reason, atr_at_split, regime, session, estimated_ticks
            ))

        return blocks

    def _create_block(
        self,
        index: int,
        start: datetime,
        end: datetime,
        split_reason: str,
        atr: float,
        regime: VolatilityRegime,
        session: TradingSession,
        estimated_ticks: int
    ) -> ProfileBlock:
        """
        Create a single ProfileBlock.

        Args:
            index: Block index
            start: Block start time
            end: Block end time
            split_reason: Why the split occurred
            atr: ATR value at split point
            regime: Volatility regime at split
            session: Trading session
            estimated_ticks: Estimated tick count

        Returns:
            ProfileBlock instance
        """
        duration_hours = (end - start).total_seconds() / 3600
        return ProfileBlock(
            block_index=index,
            start_time=start,
            end_time=end,
            block_duration_hours=round(duration_hours, 2),
            split_reason=split_reason,
            atr_at_split=round(atr, 6),
            regime_at_split=regime,
            session=session,
            estimated_ticks=estimated_ticks,
        )

    # =========================================================================
    # REGION EXTRACTION
    # =========================================================================

    def _extract_regions(
        self,
        broker_type: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime
    ) -> List[Dict]:
        """
        Extract continuous data regions within the requested time range.

        Reuses BlocksGenerator._extract_continuous_regions() pattern
        via DataCoverageReportCache.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            start_time: Requested start time
            end_time: Requested end time

        Returns:
            List of region dicts with 'start', 'end', 'following_gap', 'preceding_gap'
        """
        report = self._coverage_cache.get_report(broker_type, symbol)
        if report is None:
            return []

        # Reuse BlocksGenerator region extraction
        from python.framework.types.scenario_types.scenario_generator_types import GeneratorConfig
        dummy_config = GeneratorConfig(blocks=None)
        extractor = BlocksGenerator(config=dummy_config)
        regions = extractor._extract_continuous_regions(report)

        # Clip regions to requested time range
        clipped = []
        for region in regions:
            r_start = max(region['start'], start_time)
            r_end = min(region['end'], end_time)

            if r_end > r_start:
                clipped.append({
                    'start': r_start,
                    'end': r_end,
                    'following_gap': region['following_gap'],
                    'preceding_gap': region['preceding_gap'],
                })

        return clipped

    # =========================================================================
    # SUMMARY
    # =========================================================================

    def _print_generation_summary(
        self,
        profile: GeneratorProfile,
        mode: str
    ) -> None:
        """
        Print profile generation summary.

        Args:
            profile: Generated profile
            mode: Generation mode
        """
        meta = profile.profile_meta
        blocks = profile.blocks

        print('\n' + '=' * 60)
        print('  Profile Generation Summary')
        print('=' * 60)
        print(f"  Symbol:       {meta.symbol}")
        print(f"  Broker:       {meta.broker_type}")
        print(f"  Mode:         {mode}")
        print(f"  Blocks:       {meta.block_count}")
        print(f"  Coverage:     {meta.total_coverage_hours:.1f}h")

        if blocks:
            first_start = min(b.start_time for b in blocks)
            last_end = max(b.end_time for b in blocks)
            print(
                f"  Time range:   {first_start.strftime('%Y-%m-%d %H:%M')} → "
                f"{last_end.strftime('%Y-%m-%d %H:%M')}"
            )

            # Block size stats
            durations = [b.block_duration_hours for b in blocks]
            print(f"  Block sizes:  {min(durations):.1f}h — {max(durations):.1f}h "
                  f"(avg {sum(durations)/len(durations):.1f}h)")

            # Split reason breakdown
            reasons: Dict[str, int] = {}
            for b in blocks:
                reasons[b.split_reason] = reasons.get(b.split_reason, 0) + 1
            reason_str = ', '.join(f"{k}: {v}" for k, v in sorted(reasons.items()))
            print(f"  Split reasons: {reason_str}")

            # Regime distribution
            regimes: Dict[str, int] = {}
            for b in blocks:
                r_name = b.regime_at_split.value
                regimes[r_name] = regimes.get(r_name, 0) + 1
            regime_str = ', '.join(f"{k}: {v}" for k, v in sorted(regimes.items()))
            print(f"  Regimes:      {regime_str}")

        # Fingerprints
        if meta.discovery_fingerprints:
            print(f"  Fingerprints: {len(meta.discovery_fingerprints)} discovery caches")
        else:
            print('  Fingerprints: (none)')

        print('=' * 60 + '\n')
