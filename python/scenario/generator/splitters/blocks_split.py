"""
Blocks Split Strategy
=====================
Generates chronological time blocks with gap-aware region splitting.

Features:
- Data-start and post-gap warnings
- Detailed gap-aware warnings with color coding
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.utils.market_calendar import GapCategory, MarketCalendar
from python.framework.utils.market_session_utils import get_session_from_utc_hour
from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    BlocksStrategyConfig,
    GenerationStrategy,
)
from python.framework.types.scenario_types.window_set_types import GeneratedWindow, WindowSet
from python.scenario.generator.splitters.abstract_splitter import AbstractSplitter
from python.scenario.generator.splitters.continuous_region_extractor import ContinuousRegionExtractor
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


class BlocksSplit(AbstractSplitter):
    """
    Chronological blocks splitter.

    Generates time-based blocks with intelligent handling of:
    - Data gaps (weekend, moderate, large)
    - Data quality warnings (data-start, post-gap)
    """

    def __init__(self, config: BlocksStrategyConfig):
        """
        Initialize blocks splitter.

        Args:
            config: Blocks strategy configuration (block size + minimum)
        """
        self._config = config
        self._region_extractor = ContinuousRegionExtractor()

    def split(
        self,
        broker_type: str,
        symbol: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        count_max: Optional[int] = None,
    ) -> WindowSet:
        """
        Generate chronological blocks from continuous data regions.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbol: Trading symbol
            start_time: Optional lower bound — regions are clipped to it (None = data start)
            end_time: Optional upper bound — regions are clipped to it (None = data end)
            count_max: Optional limit on number of blocks

        Returns:
            WindowSet with chronological windows (max_ticks resolved at materialization)
        """
        block_hours = self._config.default_block_hours

        tick_index = TickIndexManager()
        tick_index.build_index()

        data_coverage_report = DataCoverageReport(
            symbol=symbol, broker_type=broker_type)
        data_coverage_report.analyze()

        # Extract continuous regions with gap information (clipped to start/end if given)
        continuous_regions = self._region_extractor.extract(
            data_coverage_report, start_time, end_time)

        if not continuous_regions:
            raise ValueError(
                f"No continuous data regions found for {broker_type}/{symbol}")

        # Log gap filtering info
        self._log_coverage_info(continuous_regions, data_coverage_report)

        # Generate blocks from continuous regions
        windows = []
        generation_warnings = []
        min_block_hours = self._config.min_block_hours
        block_duration = timedelta(hours=block_hours)

        block_counter = 0

        # Block-start snapping only applies where the market actually closes (forex
        # weekends/holidays). Crypto is 24/7 (weekend_closure=False) → a boundary is never
        # inside a closed window, so the snap must NOT fire (§37, MarketCalendar is forex-only).
        snap_to_market_open = MarketConfigManager().has_weekend_closure(broker_type)

        for region in continuous_regions:
            region_start = region['start']
            region_end = region['end']
            scenario_start = region_start

            region_duration = region_end - region_start

            vLog.debug(
                f"Region {region_start.strftime('%Y-%m-%d %H:%M')} → "
                f"{region_end.strftime('%Y-%m-%d %H:%M')} "
                f"({region_duration.total_seconds()/3600:.1f}h)"
            )

            windows_from_region = self._generate_blocks(
                region, scenario_start,
                block_duration, min_block_hours, block_hours, block_counter,
                snap_to_market_open,
            )

            # Data-start warning: first block starts at data begin
            if (region['preceding_gap'] is None
                    and region['start'] == data_coverage_report.start_time
                    and windows_from_region):
                first = windows_from_region[0]
                msg = (
                    f"Block #{block_counter + 1:02d} starts at data begin "
                    f"({first.start_time.strftime('%Y-%m-%d %H:%M')} UTC) — "
                    f"indicator warmup may be incomplete. "
                    f"Consider --start with a later date."
                )
                generation_warnings.append(msg)
                vLog.warning(f"⚠️ {msg}")

            # Post-gap warning: block follows an interrupting gap
            if region['preceding_gap'] is not None and windows_from_region:
                gap = region['preceding_gap']
                gap_name = gap.category.value.upper()
                msg = (
                    f"Block #{block_counter + 1:02d} follows a {gap_name} gap "
                    f"({gap.gap_hours:.1f}h, "
                    f"{gap.gap_start.strftime('%Y-%m-%d %H:%M')} → "
                    f"{gap.gap_end.strftime('%Y-%m-%d %H:%M')} UTC) — "
                    f"warmup data originates from before the gap."
                )
                generation_warnings.append(msg)
                vLog.warning(f"⚠️ {msg}")

            windows.extend(windows_from_region)
            block_counter += len(windows_from_region)

        # Debug: log each generated block
        for i, w in enumerate(windows, 1):
            vLog.debug(
                f"Block #{i:02d}: {w.start_time.strftime('%Y-%m-%d %H:%M')} → "
                f"{w.end_time.strftime('%Y-%m-%d %H:%M')} "
                f"({w.block_duration_hours:.1f}h) [{w.session.value}]"
            )

        # Track total before truncation
        total_generated = len(windows)

        # Apply count_max limit if specified
        if count_max and total_generated > count_max:
            windows = windows[:count_max]
            vLog.info(
                f"Limited to {count_max} blocks (from {total_generated})")
        elif count_max and total_generated < count_max:
            vLog.warning(
                f"⚠️ Requested {count_max} blocks, generated {total_generated}. "
                f"Insufficient data coverage for session filter / block size."
            )

        # Log final error if data ends with short block
        if windows:
            self._check_final_block(windows, block_hours)

        # Renumber block_index to the final 0-based position
        for i, w in enumerate(windows):
            w.block_index = i

        # Generation summary
        self._print_generation_summary(
            symbol, broker_type, block_hours, continuous_regions, windows,
            generation_warnings, total_generated
        )

        return WindowSet(
            symbol=symbol,
            broker_type=broker_type,
            strategy=GenerationStrategy.BLOCKS,
            windows=windows,
            generated_at=datetime.now(timezone.utc),
            mode=GenerationStrategy.BLOCKS.value,
        )

    # =========================================================================
    # BLOCK GENERATION
    # =========================================================================

    def _generate_blocks(
        self,
        region: Dict,
        scenario_start: datetime,
        block_duration: timedelta,
        min_block_hours: int,
        block_hours: int,
        block_counter: int,
        snap_to_market_open: bool,
    ) -> List[GeneratedWindow]:
        """
        Generate continuous chronological blocks within a data region.

        Args:
            region: Region dict with start/end/following_gap
            scenario_start: Start time for block generation
            block_duration: Target block duration
            min_block_hours: Minimum block duration
            block_hours: Target hours per block
            block_counter: Current block counter
            snap_to_market_open: When True (markets with weekend closure, i.e. forex),
                snap a boundary landing in a closed window to the next market open;
                False (crypto, 24/7) leaves boundaries untouched

        Returns:
            List of generated windows
        """
        windows = []
        region_end = region['end']
        current_start = scenario_start
        local_counter = block_counter

        while current_start < region_end:
            # Gap-aware start (forex only): a boundary landing in a market-closed window
            # (weekend / holiday) has no ticks → snap forward to the next market open (§37
            # MarketCalendar). A weekend gap inside a region never splits it, so a block
            # boundary can otherwise begin where no data exists. Crypto (24/7) is exempt —
            # snap_to_market_open is False there, so a Saturday boundary keeps its ticks.
            if snap_to_market_open and (
                    not MarketCalendar.is_market_open(current_start)
                    or MarketCalendar.is_market_holiday(current_start)):
                snapped = MarketCalendar.next_market_open(current_start)
                if snapped >= region_end:
                    break
                current_start = snapped

            remaining_hours = (
                region_end - current_start).total_seconds() / 3600

            if remaining_hours < min_block_hours:
                local_counter += 1
                gap_info = self._get_gap_info(region)
                vLog.debug(
                    f"Block #{local_counter:02d}: Skipping remainder {remaining_hours:.1f}h < {min_block_hours}h\n"
                    f"   Time: {current_start.strftime('%Y-%m-%d %H:%M')} → {region_end.strftime('%Y-%m-%d %H:%M')} UTC ({current_start.strftime('%a')})\n"
                    f"   Reason: Below minimum block duration{gap_info}"
                )
                break

            local_counter += 1

            if remaining_hours < block_hours:
                block_end = region_end
                gap_info = self._get_gap_info(region)
                vLog.debug(
                    f"Block #{local_counter:02d}: Short block {remaining_hours:.1f}h < {block_hours}h target\n"
                    f"   Time: {current_start.strftime('%Y-%m-%d %H:%M')} → {block_end.strftime('%Y-%m-%d %H:%M')} UTC ({current_start.strftime('%a')})\n"
                    f"   Reason: End of continuous data region{gap_info}"
                )
            else:
                block_end = current_start + block_duration

            start_hour = current_start.hour
            session = TradingSession(get_session_from_utc_hour(start_hour))

            windows.append(GeneratedWindow(
                block_index=0,
                start_time=current_start,
                end_time=block_end,
                regime=VolatilityRegime.MEDIUM,
                session=session,
                estimated_ticks=0,
                atr=0.0,
                tick_density=0.0,
            ))

            current_start = block_end

        return windows

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

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
        # Get allowed categories from config — only non-allowed gaps cause splits
        allowed_strings = AppConfigManager().get_allowed_gap_categories()
        allowed_categories = {
            GapCategory(cat_str) for cat_str in allowed_strings
            if cat_str in [c.value for c in GapCategory]
        }

        filtered_gaps = [
            g for g in data_coverage_report.gaps
            if g.category not in allowed_categories
        ]

        total_coverage_hours = sum(
            (r['end'] - r['start']).total_seconds() / 3600
            for r in continuous_regions
        )
        total_gap_hours = sum(g.gap_hours for g in filtered_gaps)

        vLog.info(
            f"Coverage: {total_coverage_hours:.1f}h usable, "
            f"{total_gap_hours:.1f}h gaps filtered "
            f"({len(filtered_gaps)} interrupting gaps, "
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
        windows: List[GeneratedWindow],
        block_hours: int
    ) -> None:
        """
        Check if final block is shorter than target and log error.

        Args:
            windows: Generated windows
            block_hours: Target block hours
        """
        last_block = windows[-1]
        last_duration_hours = last_block.block_duration_hours

        if last_duration_hours < block_hours:
            vLog.error(
                f"🔴 Block #{len(windows):02d}: Data ends with short block {last_duration_hours:.1f}h < {block_hours}h\n"
                f"   Time: {last_block.start_time.strftime('%Y-%m-%d %H:%M')} → {last_block.end_time.strftime('%Y-%m-%d %H:%M')} UTC ({last_block.start_time.strftime('%a')})\n"
                f"   Reason: End of available data"
            )

    def _print_generation_summary(
        self,
        symbol: str,
        broker_type: str,
        block_hours: int,
        regions: List[Dict],
        windows: List[GeneratedWindow],
        warnings: List[str],
        total_generated: int
    ) -> None:
        """
        Print structured generation summary with all warnings.

        Args:
            symbol: Trading symbol
            broker_type: Broker type identifier
            block_hours: Target block size
            regions: Continuous data regions
            windows: Generated windows
            warnings: Collected generation warnings
            total_generated: Total blocks before count_max truncation
        """
        interrupting_count = sum(
            1 for r in regions if r.get('preceding_gap') is not None
        )

        print('\n' + '=' * 60)
        print('  Generation Summary')
        print('=' * 60)
        print(f"  Symbol:      {symbol}")
        print(f"  Broker:      {broker_type}")
        print(f"  Block size:  {block_hours}h")
        print(
            f"  Regions:     {len(regions)} ({interrupting_count} interrupting gaps)")
        if total_generated > len(windows):
            print(
                f"  Blocks:      {len(windows)} (of {total_generated} available)")
        else:
            print(f"  Blocks:      {len(windows)}")

        if windows:
            first_start = min(w.start_time for w in windows)
            last_end = max(w.end_time for w in windows)
            total_hours = sum(w.block_duration_hours for w in windows)
            print(
                f"  Time range:  {first_start.strftime('%Y-%m-%d')} → "
                f"{last_end.strftime('%Y-%m-%d')}"
            )
            print(f"  Total:       {total_hours:.0f}h")

        if warnings:
            print(f"\n  Warnings ({len(warnings)}):")
            for w in warnings:
                print(f"   ⚠️ {w}")
        else:
            print('\n  Warnings:    (none)')

        print('=' * 60 + '\n')
