"""
Blocks Strategy Generator
==========================
Generates chronological time blocks with gap-aware warmup handling.

Features:
- Warmup period after interrupting gaps
- Session extension support
- Detailed gap-aware warnings with color coding
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.utils.market_calendar import GapCategory
from python.framework.utils.market_session_utils import get_session_from_utc_hour
from python.framework.types.scenario_generator_types import (
    GeneratorConfig,
    ScenarioCandidate,
    TradingSession,
    VolatilityRegime,
)
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


class BlocksGenerator:
    """
    Chronological blocks generator.

    Generates time-based blocks with intelligent handling of:
    - Data gaps (weekend, moderate, large)
    - Worker warmup periods
    - Session filtering with optional extension
    """

    def __init__(self, config: GeneratorConfig):
        """
        Initialize blocks generator.

        Args:
            data_dir: Path to processed data directory
            config: Generator configuration
        """
        self._config = config

    def generate(
        self,
        broker_type: str,
        symbol: str,
        block_hours: int,
        count_max: Optional[int],
        sessions_filter: Optional[List[str]] = None
    ) -> List[ScenarioCandidate]:
        """
        Generate chronological blocks with warmup handling after gaps.

        After interrupting gaps (MODERATE/LARGE/WEEKEND), skips warmup_hours
        before starting scenarios. Subsequent blocks use previous blocks as warmup.

        If extend_blocks_beyond_session=true: Session filter defines START points only,
        blocks run full duration. If false: Blocks constrained to session windows.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbol: Trading symbol
            block_hours: Target hours per block
            count_max: Optional limit on number of blocks
            sessions_filter: Optional list of session names to include

        Returns:
            List of scenario candidates with max_ticks=None
        """
        tick_index = TickIndexManager()
        tick_index.build_index()

        data_coverage_report = DataCoverageReport(
            symbol=symbol, broker_type=broker_type)
        data_coverage_report.analyze()

        # Extract continuous regions with gap information
        continuous_regions = self._extract_continuous_regions(
            data_coverage_report)

        if not continuous_regions:
            raise ValueError(
                f"No continuous data regions found for {broker_type}/{symbol}")

        # Convert sessions_filter to TradingSession enums
        allowed_sessions = self._parse_sessions_filter(sessions_filter)

        # Log gap filtering info
        self._log_coverage_info(continuous_regions, data_coverage_report)

        # Generate blocks from continuous regions with warmup handling
        scenarios = []
        warmup_duration = timedelta(hours=self._config.blocks.warmup_hours)
        min_block_hours = self._config.blocks.min_block_hours
        block_duration = timedelta(hours=block_hours)
        extend_beyond_session = self._config.blocks.extend_blocks_beyond_session

        block_counter = 0

        for region in continuous_regions:
            region_start = region['start']
            region_end = region['end']

            # Skip warmup period after gap
            scenario_start = region_start + warmup_duration
            region_duration = region_end - region_start
            usable_duration = region_end - scenario_start

            vLog.debug(
                f"Region {region_start.strftime('%Y-%m-%d %H:%M')} → "
                f"{region_end.strftime('%Y-%m-%d %H:%M')} "
                f"({region_duration.total_seconds()/3600:.1f}h total, "
                f"{usable_duration.total_seconds()/3600:.1f}h after warmup)"
            )

            # Check if region is too short after warmup
            if scenario_start >= region_end:
                vLog.warning(
                    f"⚠️ Region too short for {self._config.blocks.warmup_hours}h warmup, skipping"
                )
                continue

            # Generate blocks based on extend_blocks_beyond_session setting
            if extend_beyond_session and allowed_sessions:
                scenarios_from_region = self._generate_extended_blocks(
                    symbol, broker_type, region, scenario_start, allowed_sessions,
                    block_duration, min_block_hours, block_hours, block_counter
                )
            else:
                scenarios_from_region = self._generate_constrained_blocks(
                    symbol, broker_type, region, scenario_start, allowed_sessions,
                    block_duration, min_block_hours, block_hours, block_counter
                )

            scenarios.extend(scenarios_from_region)
            block_counter += len(scenarios_from_region)

        # Debug: log each generated block
        for i, s in enumerate(scenarios, 1):
            vLog.debug(
                f"Block #{i:02d}: {s.start_time.strftime('%Y-%m-%d %H:%M')} → "
                f"{s.end_time.strftime('%Y-%m-%d %H:%M')} "
                f"({(s.end_time - s.start_time).total_seconds()/3600:.1f}h) [{s.session.value}]"
            )

        # Apply count_max limit if specified
        if count_max and len(scenarios) > count_max:
            total_generated = len(scenarios)
            scenarios = scenarios[:count_max]
            vLog.info(
                f"Limited to {count_max} blocks (from {total_generated})")
        elif count_max and len(scenarios) < count_max:
            vLog.warning(
                f"⚠️ Requested {count_max} blocks, generated {len(scenarios)}. "
                f"Insufficient data coverage for session filter / block size."
            )

        # Log final error if data ends with short block
        if scenarios:
            self._check_final_block(scenarios, block_hours)

        return scenarios

    # =========================================================================
    # BLOCK GENERATION STRATEGIES
    # =========================================================================

    def _generate_extended_blocks(
        self,
        symbol: str,
        broker_type: str,
        region: Dict,
        scenario_start: datetime,
        allowed_sessions: set,
        block_duration: timedelta,
        min_block_hours: int,
        block_hours: int,
        block_counter: int
    ) -> List[ScenarioCandidate]:
        """
        Generate blocks with session extension enabled.

        Session filter defines START points, blocks run full duration.

        Args:
            symbol: Trading symbol
            region: Region dict with start/end/following_gap
            scenario_start: Start time after warmup
            allowed_sessions: Set of allowed TradingSession enums
            block_duration: Target block duration
            min_block_hours: Minimum block duration
            block_hours: Target hours per block
            block_counter: Current block counter

        Returns:
            List of scenario candidates
        """
        scenarios = []
        region_end = region['end']

        session_start_points = self._extract_session_start_points(
            scenario_start, region_end, allowed_sessions
        )

        current_start = scenario_start
        local_counter = block_counter

        for start_point in session_start_points:
            if current_start >= region_end:
                break

            # Align to session start if we haven't passed it
            if start_point >= current_start:
                current_start = start_point

            remaining_hours = (
                region_end - current_start).total_seconds() / 3600

            if remaining_hours < min_block_hours:
                local_counter += 1
                gap_info = self._get_gap_info(region)
                vLog.warning(
                    f"⚠️ Block #{local_counter:02d}: Skipping remainder {remaining_hours:.1f}h < {min_block_hours}h\n"
                    f"   Time: {current_start.strftime('%Y-%m-%d %H:%M')} → {region_end.strftime('%Y-%m-%d %H:%M')} UTC ({current_start.strftime('%a')})\n"
                    f"   Reason: Below minimum block duration{gap_info}"
                )
                break

            local_counter += 1

            if remaining_hours < block_hours:
                # Last block - shorter than target
                block_end = region_end
                gap_info = self._get_gap_info(region)
                vLog.warning(
                    f"⚠️ Block #{local_counter:02d}: Short block {remaining_hours:.1f}h < {block_hours}h target\n"
                    f"   Time: {current_start.strftime('%Y-%m-%d %H:%M')} → {block_end.strftime('%Y-%m-%d %H:%M')} UTC ({current_start.strftime('%a')})\n"
                    f"   Reason: End of continuous data region{gap_info}"
                )
            else:
                block_end = current_start + block_duration

            # Get session at start
            start_hour = current_start.hour
            session = TradingSession(get_session_from_utc_hour(start_hour))

            scenarios.append(ScenarioCandidate(
                symbol=symbol,
                start_time=current_start,
                end_time=block_end,
                broker_type=broker_type,
                regime=VolatilityRegime.MEDIUM,  # Default for blocks
                session=session,
                estimated_ticks=0,  # Time-based, no tick limit
                atr=0.0,
                tick_density=0.0,
                real_bar_ratio=1.0
            ))

            current_start = block_end

        return scenarios

    def _generate_constrained_blocks(
        self,
        symbol: str,
        broker_type: str,
        region: Dict,
        scenario_start: datetime,
        allowed_sessions: Optional[set],
        block_duration: timedelta,
        min_block_hours: int,
        block_hours: int,
        block_counter: int
    ) -> List[ScenarioCandidate]:
        """
        Generate blocks constrained to session windows.

        Args:
            symbol: Trading symbol
            region: Region dict with start/end/following_gap
            scenario_start: Start time after warmup
            allowed_sessions: Set of allowed TradingSession enums (or None for all)
            block_duration: Target block duration
            min_block_hours: Minimum block duration
            block_hours: Target hours per block
            block_counter: Current block counter

        Returns:
            List of scenario candidates
        """
        scenarios = []
        region_end = region['end']

        # If no session filter, generate continuous blocks
        if not allowed_sessions:
            current_start = scenario_start
            local_counter = block_counter

            while current_start < region_end:
                remaining_hours = (
                    region_end - current_start).total_seconds() / 3600

                if remaining_hours < min_block_hours:
                    local_counter += 1
                    gap_info = self._get_gap_info(region)
                    vLog.warning(
                        f"⚠️ Block #{local_counter:02d}: Skipping remainder {remaining_hours:.1f}h < {min_block_hours}h\n"
                        f"   Time: {current_start.strftime('%Y-%m-%d %H:%M')} → {region_end.strftime('%Y-%m-%d %H:%M')} UTC ({current_start.strftime('%a')})\n"
                        f"   Reason: Below minimum block duration{gap_info}"
                    )
                    break

                local_counter += 1

                if remaining_hours < block_hours:
                    block_end = region_end
                    gap_info = self._get_gap_info(region)
                    vLog.warning(
                        f"⚠️ Block #{local_counter:02d}: Short block {remaining_hours:.1f}h < {block_hours}h target\n"
                        f"   Time: {current_start.strftime('%Y-%m-%d %H:%M')} → {block_end.strftime('%Y-%m-%d %H:%M')} UTC ({current_start.strftime('%a')})\n"
                        f"   Reason: End of continuous data region{gap_info}"
                    )
                else:
                    block_end = current_start + block_duration

                start_hour = current_start.hour
                session = TradingSession(get_session_from_utc_hour(start_hour))

                scenarios.append(ScenarioCandidate(
                    symbol=symbol,
                    start_time=current_start,
                    end_time=block_end,
                    broker_type=broker_type,
                    regime=VolatilityRegime.MEDIUM,
                    session=session,
                    estimated_ticks=0,
                    atr=0.0,
                    tick_density=0.0,
                    real_bar_ratio=1.0
                ))

                current_start = block_end

            return scenarios

        # Session-constrained blocks
        session_windows = self._extract_session_windows(
            scenario_start, region_end, allowed_sessions
        )

        local_counter = block_counter

        for window in session_windows:
            window_start = window['start']
            window_end = window['end']
            current_start = window_start

            while current_start < window_end:
                remaining_hours = (
                    window_end - current_start).total_seconds() / 3600

                if remaining_hours < min_block_hours:
                    break

                local_counter += 1

                if remaining_hours < block_hours:
                    block_end = window_end
                else:
                    block_end = current_start + block_duration
                    if block_end > window_end:
                        block_end = window_end

                start_hour = current_start.hour
                session = TradingSession(get_session_from_utc_hour(start_hour))

                scenarios.append(ScenarioCandidate(
                    symbol=symbol,
                    start_time=current_start,
                    end_time=block_end,
                    broker_type=broker_type,
                    regime=VolatilityRegime.MEDIUM,
                    session=session,
                    estimated_ticks=0,
                    atr=0.0,
                    tick_density=0.0,
                    real_bar_ratio=1.0
                ))

                current_start = block_end

        return scenarios

    # =========================================================================
    # COVERAGE AND GAP HANDLING
    # =========================================================================

    def _extract_continuous_regions(
        self,
        data_coverage_report: DataCoverageReport
    ) -> List[Dict]:
        """
        Extract continuous data regions from coverage report.

        Splits timeline at MODERATE/LARGE/WEEKEND gaps.
        SMALL gaps are ignored (treated as continuous).

        Args:
            data_coverage_report: Coverage report with gap analysis

        Returns:
            List of region dicts with 'start', 'end', 'following_gap'
        """
        regions = []

        # Filter for interrupting gaps only
        interrupting_gaps = [
            g for g in data_coverage_report.gaps
            if g.category in [GapCategory.MODERATE, GapCategory.LARGE, GapCategory.WEEKEND, GapCategory.HOLIDAY]
        ]

        if not interrupting_gaps:
            # No interrupting gaps - single continuous region
            return [{
                'start': data_coverage_report.start_time,
                'end': data_coverage_report.end_time,
                'following_gap': None
            }]

        # Sort gaps by start time
        interrupting_gaps = sorted(
            interrupting_gaps, key=lambda g: g.gap_start)

        # Build regions between gaps
        current_start = data_coverage_report.start_time

        for gap in interrupting_gaps:
            if gap.gap_start <= current_start:
                # Gap before or at current position
                current_start = gap.gap_end
                continue

            # End current region at gap start
            region_end = gap.gap_start

            if region_end > current_start:
                regions.append({
                    'start': current_start,
                    'end': region_end,
                    'following_gap': gap  # Store gap info for warnings
                })

            # Start new region after gap
            current_start = gap.gap_end

        # Add final region (no following gap)
        final_end = data_coverage_report.end_time
        if final_end > current_start:
            regions.append({
                'start': current_start,
                'end': final_end,
                'following_gap': None
            })

        return regions

    def _extract_session_start_points(
        self,
        start: datetime,
        end: datetime,
        allowed_sessions: set
    ) -> List[datetime]:
        """
        Extract session start points within time range.

        Used when extend_blocks_beyond_session=true to find valid block start times.

        Args:
            start: Region start time
            end: Region end time
            allowed_sessions: Set of TradingSession enums to include

        Returns:
            List of datetime objects marking session starts
        """
        start_points = []
        current_hour = start.replace(minute=0, second=0, microsecond=0)
        prev_session = None

        while current_hour < end:
            hour_session = get_session_from_utc_hour(current_hour.hour)

            # Detect session start (transition from non-allowed to allowed)
            if hour_session in allowed_sessions and prev_session != hour_session:
                actual_start = max(current_hour, start)
                if actual_start < end:
                    start_points.append(actual_start)

            prev_session = hour_session
            current_hour += timedelta(hours=1)

        return start_points

    def _extract_session_windows(
        self,
        start: datetime,
        end: datetime,
        allowed_sessions: set
    ) -> List[Dict[str, datetime]]:
        """
        Extract time windows within allowed trading sessions.

        Args:
            start: Region start time
            end: Region end time
            allowed_sessions: Set of TradingSession enums to include

        Returns:
            List of dicts with 'start' and 'end' datetime keys
        """
        windows = []
        current_window_start = None
        current_hour = start.replace(minute=0, second=0, microsecond=0)

        while current_hour < end:
            hour_session = get_session_from_utc_hour(current_hour.hour)

            if hour_session in allowed_sessions:
                if current_window_start is None:
                    current_window_start = max(current_hour, start)
            else:
                if current_window_start is not None:
                    window_end = min(current_hour, end)
                    if window_end > current_window_start:
                        windows.append({
                            'start': current_window_start,
                            'end': window_end
                        })
                    current_window_start = None

            current_hour += timedelta(hours=1)

        # Close final window if open
        if current_window_start is not None:
            windows.append({
                'start': current_window_start,
                'end': end
            })

        return windows

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _parse_sessions_filter(
        self,
        sessions_filter: Optional[List[str]]
    ) -> Optional[set]:
        """
        Parse sessions filter strings to TradingSession enums.

        Args:
            sessions_filter: Optional list of session name strings

        Returns:
            Set of TradingSession enums or None
        """
        if not sessions_filter:
            return None

        allowed_sessions = set()
        for s in sessions_filter:
            try:
                allowed_sessions.add(TradingSession(s))
            except ValueError:
                vLog.warning(f"Unknown session '{s}', ignoring")

        if allowed_sessions:
            vLog.info(
                f"Filtering blocks to sessions: {[s.value for s in allowed_sessions]}")
            return allowed_sessions

        return None

    def _log_coverage_info(
        self,
        continuous_regions: List[Dict],
        data_coverage_report: DataCoverageReport
    ) -> None:
        """
        Log coverage and gap statistics.

        Args:
            continuous_regions: Extracted continuous regions
            data_coverage_report: Coverage report
        """
        filtered_gaps = [
            g for g in data_coverage_report.gaps
            if g.category in [GapCategory.MODERATE, GapCategory.LARGE, GapCategory.WEEKEND, GapCategory.HOLIDAY]
        ]

        total_coverage_hours = sum(
            (r['end'] - r['start']).total_seconds() / 3600
            for r in continuous_regions
        )
        total_gap_hours = sum(g.gap_hours for g in filtered_gaps)

        vLog.info(
            f"Coverage: {total_coverage_hours:.1f}h usable, "
            f"{total_gap_hours:.1f}h gaps filtered "
            f"({len(filtered_gaps)} gaps: "
            f"{data_coverage_report.gap_counts['weekend']} weekend, "
            f"{data_coverage_report.gap_counts['holiday']} holiday, "
            f"{data_coverage_report.gap_counts['moderate']} moderate, "
            f"{data_coverage_report.gap_counts['large']} large)"
        )

    def _get_gap_info(self, region: Dict) -> str:
        """
        Extract gap information for detailed warnings.

        Args:
            region: Region dict with 'following_gap' key

        Returns:
            Formatted string with gap type, duration, and icon
        """
        if not region.get('following_gap'):
            return ""

        gap = region['following_gap']

        # Color coding by gap type
        icons = {
            GapCategory.WEEKEND: '🟢',
            GapCategory.HOLIDAY: '🟢',
            GapCategory.MODERATE: '🟡',
            GapCategory.LARGE: '🔴'
        }

        icon = icons.get(gap.category, '❓')
        gap_name = gap.category.value.replace('_', ' ').title()

        return f" - {gap_name} gap follows ({gap.gap_hours:.1f}h) {icon}"

    def _check_final_block(
        self,
        scenarios: List[ScenarioCandidate],
        block_hours: int
    ) -> None:
        """
        Check if final block is shorter than target and log error.

        Args:
            scenarios: Generated scenarios
            block_hours: Target block hours
        """
        last_block = scenarios[-1]
        last_duration_hours = (
            last_block.end_time - last_block.start_time).total_seconds() / 3600

        if last_duration_hours < block_hours:
            vLog.error(
                f"🔴 Block #{len(scenarios):02d}: Data ends with short block {last_duration_hours:.1f}h < {block_hours}h\n"
                f"   Time: {last_block.start_time.strftime('%Y-%m-%d %H:%M')} → {last_block.end_time.strftime('%Y-%m-%d %H:%M')} UTC ({last_block.start_time.strftime('%a')})\n"
                f"   Reason: End of available data"
            )
