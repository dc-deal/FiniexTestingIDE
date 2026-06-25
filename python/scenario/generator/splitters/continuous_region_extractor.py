"""
Continuous Region Extractor
===========================
Shared gap-aware region extraction for all splitters.

Splits a data-coverage timeline into continuous regions at gaps that are NOT in the
configured allowed-gap categories (weekend / holiday / seamless / short are treated as
continuous; only moderate / large gaps cause a region split). Optionally clips the regions
to a requested time range.

This is the single source for region extraction — previously duplicated as a private method
on BlocksGenerator that ProfileGenerator reached into across class boundaries.
"""

from datetime import datetime
from typing import Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.utils.market_calendar import GapCategory


class ContinuousRegionExtractor:
    """Extracts continuous data regions from a coverage report (gap-aware)."""

    def extract(
        self,
        data_coverage_report: DataCoverageReport,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        """
        Extract continuous data regions, splitting only at interrupting gaps.

        Args:
            data_coverage_report: Coverage report with gap analysis
            start_time: Optional clip start (regions are clipped to this lower bound)
            end_time: Optional clip end (regions are clipped to this upper bound)

        Returns:
            List of region dicts with 'start', 'end', 'following_gap', 'preceding_gap'
        """
        regions = self._extract_full(data_coverage_report)

        if start_time is None and end_time is None:
            return regions

        return self._clip(regions, start_time, end_time)

    def _extract_full(
        self,
        data_coverage_report: DataCoverageReport,
    ) -> List[Dict]:
        """
        Extract continuous data regions from coverage report.

        Splits timeline at gaps that are NOT in allowed_gap_categories. Allowed gaps
        (e.g. weekend, holiday, seamless, short) are treated as continuous — regions
        may span across them.

        Args:
            data_coverage_report: Coverage report with gap analysis

        Returns:
            List of region dicts with 'start', 'end', 'following_gap', 'preceding_gap'
        """
        regions = []

        # Get allowed categories from config — split only at non-allowed gaps
        allowed_strings = AppConfigManager().get_allowed_gap_categories()
        allowed_categories = {
            GapCategory(cat_str) for cat_str in allowed_strings
            if cat_str in [c.value for c in GapCategory]
        }

        # Filter for interrupting gaps: those NOT in allowed categories
        interrupting_gaps = [
            g for g in data_coverage_report.gaps
            if g.category not in allowed_categories
        ]

        if not interrupting_gaps:
            # No interrupting gaps - single continuous region
            return [{
                'start': data_coverage_report.start_time,
                'end': data_coverage_report.end_time,
                'following_gap': None,
                'preceding_gap': None
            }]

        # Sort gaps by start time
        interrupting_gaps = sorted(
            interrupting_gaps, key=lambda g: g.gap_start)

        # Build regions between gaps
        current_start = data_coverage_report.start_time
        preceding_gap = None

        for gap in interrupting_gaps:
            if gap.gap_start <= current_start:
                # Gap before or at current position
                current_start = gap.gap_end
                preceding_gap = gap
                continue

            # End current region at gap start
            region_end = gap.gap_start

            if region_end > current_start:
                regions.append({
                    'start': current_start,
                    'end': region_end,
                    'following_gap': gap,  # Store gap info for warnings
                    'preceding_gap': preceding_gap
                })

            # Start new region after gap
            preceding_gap = gap
            current_start = gap.gap_end

        # Add final region (no following gap)
        final_end = data_coverage_report.end_time
        if final_end > current_start:
            regions.append({
                'start': current_start,
                'end': final_end,
                'following_gap': None,
                'preceding_gap': preceding_gap
            })

        return regions

    def _clip(
        self,
        regions: List[Dict],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> List[Dict]:
        """
        Clip regions to the requested time range.

        Args:
            regions: Full continuous regions
            start_time: Lower bound (None = no lower clip)
            end_time: Upper bound (None = no upper clip)

        Returns:
            List of clipped region dicts (empty-after-clip regions dropped)
        """
        clipped = []
        for region in regions:
            r_start = max(region['start'], start_time) if start_time else region['start']
            r_end = min(region['end'], end_time) if end_time else region['end']

            if r_end > r_start:
                clipped.append({
                    'start': r_start,
                    'end': r_end,
                    'following_gap': region['following_gap'],
                    'preceding_gap': region['preceding_gap'],
                })

        return clipped
