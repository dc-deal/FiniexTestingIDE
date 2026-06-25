"""
Volatility Split Strategy
=========================
Generates windows by splitting continuous regions at volatility minima (low-ATR periods),
minimizing trade disruption at block boundaries.
"""

from datetime import datetime, timedelta
from typing import Dict, List

import numpy as np

from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityPeriod,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import GenerationStrategy
from python.framework.types.scenario_types.window_set_types import GeneratedWindow
from python.scenario.generator.splitters.abstract_profile_splitter import AbstractProfileSplitter


class VolatilitySplit(AbstractProfileSplitter):
    """ATR-minima splitter — cuts regions at low-volatility periods."""

    def _get_strategy(self) -> GenerationStrategy:
        """
        The generation strategy this splitter implements.

        Returns:
            GenerationStrategy.VOLATILITY_SPLIT
        """
        return GenerationStrategy.VOLATILITY_SPLIT

    def _build_windows(
        self,
        regions: List[Dict],
        periods: List[VolatilityPeriod]
    ) -> List[GeneratedWindow]:
        """
        Generate windows by splitting at ATR minima within continuous regions.

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
            List of GeneratedWindow
        """
        all_windows = []
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
                all_windows.append(self._create_window(
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

            # Build windows from split points
            region_windows = self._build_windows_from_splits(
                block_index, region_start, region_end,
                split_points, region_periods
            )

            all_windows.extend(region_windows)
            block_index += len(region_windows)

        # Calculate distance_to_next_block_hours
        for i in range(len(all_windows) - 1):
            distance = (all_windows[i + 1].start_time - all_windows[i].end_time).total_seconds() / 3600
            all_windows[i].distance_to_next_block_hours = round(distance, 2)

        return all_windows

    def _find_split_points(
        self,
        region_start: datetime,
        region_end: datetime,
        periods: List[VolatilityPeriod],
        atr_threshold: float
    ) -> List[datetime]:
        """
        Find optimal split points using window-based ATR-minima selection.

        Walks forward in max_block_hours windows, only splitting when the remaining
        region exceeds max_block_hours. Within each window, picks the candidate with the
        lowest ATR. This avoids mini-block proliferation (no unnecessary splits) and
        guarantees no block exceeds max_block_hours (iterative convergence).

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

        # ATR-minima candidates sorted chronologically
        candidates = sorted(
            [p for p in periods if p.atr <= atr_threshold],
            key=lambda p: p.start_time
        )

        split_points = []
        current_start = region_start

        while True:
            remaining_hours = (region_end - current_start).total_seconds() / 3600

            # If remaining fits in one block, we're done
            if remaining_hours <= max_hours:
                break

            # Search window: [current + min_hours, current + max_hours]
            window_min = current_start + timedelta(hours=min_hours)
            window_max = current_start + timedelta(hours=max_hours)

            # Find ATR-minima candidates within the window
            valid = [
                c for c in candidates
                if window_min <= c.start_time <= window_max
                and (region_end - c.start_time).total_seconds() / 3600 >= min_hours
            ]

            if valid:
                # Pick lowest ATR candidate
                best = min(valid, key=lambda c: c.atr)
                split_points.append(best.start_time)
                current_start = best.start_time
            else:
                # No ATR-minima candidate — try any period with lowest ATR
                forced = [
                    p for p in periods
                    if window_min <= p.start_time <= window_max
                    and (region_end - p.start_time).total_seconds() / 3600 >= min_hours
                ]

                if forced:
                    best = min(forced, key=lambda p: p.atr)
                    self._logger.warning(
                        f"⚠️ Forced split at {best.start_time.strftime('%Y-%m-%d %H:%M')} "
                        f"(ATR={best.atr:.4f}) — no ATR-minima within {max_hours}h"
                    )
                    split_points.append(best.start_time)
                    current_start = best.start_time
                else:
                    # No periods in window — likely a gap (weekend/holiday).
                    # Skip forward to the next available period instead of
                    # inserting artificial splits into empty time ranges.
                    next_periods = [
                        p for p in periods
                        if p.start_time > window_max
                    ]
                    if next_periods:
                        current_start = next_periods[0].start_time
                    else:
                        break

        return sorted(set(split_points))

    def _build_windows_from_splits(
        self,
        start_index: int,
        region_start: datetime,
        region_end: datetime,
        split_points: List[datetime],
        periods: List[VolatilityPeriod]
    ) -> List[GeneratedWindow]:
        """
        Build GeneratedWindow list from split points.

        Args:
            start_index: Starting window index
            region_start: Region start time
            region_end: Region end time
            split_points: Sorted split point times
            periods: Volatility periods for metadata

        Returns:
            List of GeneratedWindow
        """
        boundaries = [region_start] + sorted(split_points) + [region_end]
        windows = []

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

            windows.append(self._create_window(
                start_index + i, block_start, block_end,
                split_reason, atr_at_split, regime, session, estimated_ticks
            ))

        return windows
