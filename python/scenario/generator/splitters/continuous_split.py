"""
Continuous Split Strategy
=========================
Generates one window per continuous data region (no intra-region splitting).
"""

from typing import Dict, List

from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityPeriod,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import GenerationStrategy
from python.framework.types.scenario_types.window_set_types import GeneratedWindow
from python.scenario.generator.splitters.abstract_profile_splitter import AbstractProfileSplitter


class ContinuousSplit(AbstractProfileSplitter):
    """One window per continuous data region — no splitting."""

    def _get_strategy(self) -> GenerationStrategy:
        """
        The generation strategy this splitter implements.

        Returns:
            GenerationStrategy.CONTINUOUS
        """
        return GenerationStrategy.CONTINUOUS

    def _build_windows(
        self,
        regions: List[Dict],
        periods: List[VolatilityPeriod]
    ) -> List[GeneratedWindow]:
        """
        Generate one window per continuous data region (no splitting).

        Args:
            regions: Continuous data regions
            periods: Volatility periods for metadata

        Returns:
            List of GeneratedWindow (one per region)
        """
        windows = []

        for i, region in enumerate(regions):
            region_start = region['start']
            region_end = region['end']

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

            windows.append(self._create_window(
                index=i,
                start=region_start,
                end=region_end,
                split_reason='continuous_region',
                atr=atr_at_split,
                regime=regime,
                session=session,
                estimated_ticks=estimated_ticks,
                distance_to_next_block_hours=round(distance, 2) if distance is not None else None,
            ))

        return windows
