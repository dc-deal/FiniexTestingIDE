"""
High-Volatility Strategy Generator
====================================
Generates scenarios centered on high-volatility periods.

Features:
- Filters HIGH/VERY_HIGH volatility regimes
- Centers scenario windows around volatility peaks (with hour alignment)
- Warmup-aware with gap checking
- No overlap between scenarios
- Enhanced debug logging for diagnostics
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from python.framework.discoveries.market_analyzer.market_analyzer import MarketAnalyzer
from python.framework.types.market_types.market_analysis_types import (
    PeriodAnalysis,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    GeneratorConfig,
    ScenarioCandidate,
)
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


class HighVolatilityGenerator:
    """
    High-volatility scenario generator.

    Selects high-volatility periods and builds scenarios centered
    around them, ensuring proper warmup and no overlap.
    """

    def __init__(self, config: GeneratorConfig, analyzer: MarketAnalyzer):
        """
        Initialize high-volatility generator.

        Args:
            config: Generator configuration
            analyzer: Market analyzer for period extraction
        """
        self._config = config
        self._analyzer = analyzer

    def generate(
        self,
        broker_type: str,
        symbol: str,
        block_hours: int,
        count: int,
        max_ticks: Optional[int] = None
    ) -> List[ScenarioCandidate]:
        """
        Generate scenarios centered on high-volatility periods.

        Algorithm:
        1. Get HIGH/VERY_HIGH volatility periods
        2. Sort by tick_count (highest activity first)
        3. For each high-volatility period:
           - Center scenario window around it (aligned to hour boundaries)
           - Ensure warmup data available
           - Check for gaps in window
           - Verify no overlap with existing scenarios

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbol: Trading symbol
            block_hours: Scenario duration in hours
            count: Number of scenarios to generate
            max_ticks: Unused for high-volatility (time-based only)

        Returns:
            List of scenario candidates
        """
        # Get high-volatility periods (HIGH/VERY_HIGH regimes)
        high_vol_periods = self._analyzer.get_high_volatility_periods(broker_type, symbol)

        if not high_vol_periods:
            raise ValueError(
                f"No HIGH/VERY_HIGH volatility periods found for {broker_type}/{symbol}"
            )

        vLog.info(
            f"Generating {count} high-volatility scenarios from {len(high_vol_periods)} "
            f"high-volatility periods"
        )

        # Get all periods for gap checking
        all_periods = self._analyzer.get_periods(broker_type, symbol)

        # Build centered scenarios
        scenarios = []
        used_ranges: List[Tuple[datetime, datetime]] = []

        warmup_hours = self._config.high_volatility.warmup_hours
        min_real_bar_ratio = self._config.high_volatility.min_real_bar_ratio

        # Skip reason tracking
        skip_reasons: Dict[str, int] = {
            'insufficient_warmup': 0,
            'gap_detected': 0,
            'overlap': 0,
            'low_quality': 0
        }

        # Data boundaries
        data_start = min(p.start_time for p in all_periods)
        data_end = max(p.end_time for p in all_periods)

        vLog.info(
            f"Data range: {data_start.strftime('%Y-%m-%d %H:%M')} → "
            f"{data_end.strftime('%Y-%m-%d %H:%M')}"
        )
        vLog.info(
            f"Config: {block_hours}h scenarios, {warmup_hours}h warmup, "
            f"{min_real_bar_ratio:.1%} min real-bar ratio"
        )

        detailed_log_count = 0
        max_detailed_logs = 5

        for idx, high_vol_period in enumerate(high_vol_periods, 1):
            if len(scenarios) >= count:
                break

            # Build scenario centered on high-volatility period
            result = self._build_centered_scenario(
                broker_type,
                symbol,
                high_vol_period,
                all_periods,
                block_hours,
                warmup_hours,
                min_real_bar_ratio,
                used_ranges,
                data_start,
                skip_reasons,
                detailed_log=(detailed_log_count < max_detailed_logs)
            )

            if result is None:
                if detailed_log_count < max_detailed_logs:
                    detailed_log_count += 1
                continue

            scenario, scenario_range = result
            scenarios.append(scenario)
            used_ranges.append(scenario_range)

            vLog.info(
                f"✔ High-Vol #{len(scenarios):02d}: "
                f"{high_vol_period.start_time.strftime('%Y-%m-%d %H:%M')} "
                f"({high_vol_period.regime.value}, {high_vol_period.tick_count:,} ticks)"
            )

        # Summary
        total_skipped = sum(skip_reasons.values())

        vLog.info("")
        vLog.info("=" * 60)
        vLog.info("HIGH-VOLATILITY GENERATION SUMMARY")
        vLog.info("=" * 60)
        vLog.info(f"Total candidates: {len(high_vol_periods)}")
        vLog.info(f"Scenarios generated: {len(scenarios)}")
        vLog.info(f"Scenarios skipped: {total_skipped}")

        if total_skipped > 0:
            vLog.info("")
            vLog.info("Skip reasons breakdown:")
            for reason, count in skip_reasons.items():
                if count > 0:
                    pct = (count / len(high_vol_periods)) * 100
                    vLog.info(
                        f"  • {reason.replace('_', ' ').title()}: {count} ({pct:.1f}%)")

        vLog.info("=" * 60)

        if len(scenarios) < count:
            vLog.warning(
                f"⚠️ Generated {len(scenarios)} scenarios (requested {count}). "
                f"Consider: shorter scenarios, less warmup, or more lenient gap tolerance."
            )

        return scenarios

    def _build_centered_scenario(
        self,
        broker_type: str,
        symbol: str,
        high_vol_period: PeriodAnalysis,
        all_periods: List[PeriodAnalysis],
        block_hours: int,
        warmup_hours: int,
        min_real_bar_ratio: float,
        used_ranges: List[Tuple[datetime, datetime]],
        data_start: datetime,
        skip_reasons: Dict[str, int],
        detailed_log: bool = False
    ) -> Optional[Tuple[ScenarioCandidate, Tuple[datetime, datetime]]]:
        """
        Build scenario centered on high-volatility period.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            high_vol_period: High-volatility period to center on
            all_periods: All periods for gap checking
            block_hours: Scenario duration
            warmup_hours: Warmup duration
            min_real_bar_ratio: Minimum real bar ratio
            used_ranges: Already used time ranges
            data_start: Data start boundary
            skip_reasons: Dict to track skip reasons
            detailed_log: Enable detailed logging for this period

        Returns:
            (ScenarioCandidate, time_range) or None if invalid
        """
        # Calculate scenario window centered on high-volatility period
        vol_center = high_vol_period.start_time + timedelta(minutes=30)
        scenario_start = vol_center - timedelta(hours=block_hours / 2)

        # CRITICAL FIX: Align to hour boundary (floor)
        # Periods are 1h blocks on full hours (09:00, 10:00, 11:00, ...)
        # Without alignment, 08:30 start would create gap with 09:00 first period
        scenario_start = scenario_start.replace(
            minute=0, second=0, microsecond=0)
        scenario_end = scenario_start + timedelta(hours=block_hours)

        # Calculate warmup start
        warmup_start = scenario_start - timedelta(hours=warmup_hours)

        if detailed_log:
            vLog.info("")
            vLog.info(f"Checking period: {high_vol_period.start_time.strftime('%Y-%m-%d %H:%M')} "
                      f"({high_vol_period.tick_count:,} ticks)")
            vLog.info(f"  Volatility center: {vol_center.strftime('%H:%M')}")
            vLog.info(f"  Warmup: {warmup_start.strftime('%Y-%m-%d %H:%M')} → "
                      f"{scenario_start.strftime('%Y-%m-%d %H:%M')} ({warmup_hours}h)")
            vLog.info(f"  Scenario: {scenario_start.strftime('%Y-%m-%d %H:%M')} → "
                      f"{scenario_end.strftime('%Y-%m-%d %H:%M')} ({block_hours}h)")

        # Check 1: Warmup data available?
        if warmup_start < data_start:
            skip_reasons['insufficient_warmup'] += 1
            if detailed_log:
                hours_short = (
                    data_start - warmup_start).total_seconds() / 3600
                vLog.info(f"  ✗ SKIP: Insufficient warmup data")
                vLog.info(f"    Need data from {warmup_start.strftime('%Y-%m-%d %H:%M')}, "
                          f"but data starts {data_start.strftime('%Y-%m-%d %H:%M')} "
                          f"({hours_short:.1f}h short)")
            return None

        # Check 2: Quality threshold
        real_ratio = high_vol_period.real_bar_count / \
            max(high_vol_period.bar_count, 1)
        if real_ratio < min_real_bar_ratio:
            skip_reasons['low_quality'] += 1
            if detailed_log:
                vLog.info(f"  ✗ SKIP: Low real bar ratio")
                vLog.info(
                    f"    Period has {real_ratio:.1%} real bars (need {min_real_bar_ratio:.1%})")
            return None

        # Check 3: Gap in scenario window?
        gap_info = self._check_gap_in_window(
            warmup_start, scenario_end, all_periods)
        if gap_info:
            skip_reasons['gap_detected'] += 1
            if detailed_log:
                vLog.info(f"  ✗ SKIP: Gap detected in window")
                vLog.info(f"    {gap_info}")
            return None

        # Check 4: Overlap with existing scenarios?
        if self._has_overlap(scenario_start, scenario_end, used_ranges):
            skip_reasons['overlap'] += 1
            if detailed_log:
                vLog.info(f"  ✗ SKIP: Overlaps with existing scenario")
            return None

        if detailed_log:
            vLog.info(f"  ✔ VALID: All checks passed")

        # Create scenario
        scenario = ScenarioCandidate(
            symbol=symbol,
            start_time=scenario_start,
            end_time=scenario_end,
            broker_type=broker_type,
            regime=high_vol_period.regime,
            session=high_vol_period.session,
            estimated_ticks=0,  # Time-based, no tick limit
            atr=high_vol_period.atr,
            tick_density=high_vol_period.tick_density,
            real_bar_ratio=real_ratio
        )

        return scenario, (scenario_start, scenario_end)

    def _check_gap_in_window(
        self,
        window_start: datetime,
        window_end: datetime,
        periods: List[PeriodAnalysis]
    ) -> Optional[str]:
        """
        Check if time window has gaps (missing periods).

        Periods are 1h blocks. If continuous: end of period N == start of period N+1.

        Args:
            window_start: Window start time
            window_end: Window end time
            periods: All available periods

        Returns:
            Error description if gap detected, None otherwise
        """
        # Find periods in window
        window_periods = [
            p for p in periods
            if p.start_time >= window_start and p.end_time <= window_end
        ]

        if not window_periods:
            return f"No data coverage in window"

        # Sort by time
        window_periods = sorted(window_periods, key=lambda p: p.start_time)

        # Check for gaps between consecutive periods
        for i in range(len(window_periods) - 1):
            current_end = window_periods[i].end_time
            next_start = window_periods[i + 1].start_time

            if current_end != next_start:
                gap_hours = (next_start - current_end).total_seconds() / 3600
                return (f"Gap between {current_end.strftime('%Y-%m-%d %H:%M')} and "
                        f"{next_start.strftime('%Y-%m-%d %H:%M')} ({gap_hours:.1f}h)")

        # Check if window is fully covered
        if window_periods[0].start_time > window_start:
            gap_hours = (window_periods[0].start_time -
                         window_start).total_seconds() / 3600
            return f"Gap at window start ({gap_hours:.1f}h missing)"

        if window_periods[-1].end_time < window_end:
            gap_hours = (
                window_end - window_periods[-1].end_time).total_seconds() / 3600
            return f"Gap at window end ({gap_hours:.1f}h missing)"

        return None  # No gaps

    def _has_overlap(
        self,
        start: datetime,
        end: datetime,
        used_ranges: List[Tuple[datetime, datetime]]
    ) -> bool:
        """
        Check if time range overlaps with any used range.

        Args:
            start: Range start
            end: Range end
            used_ranges: List of (start, end) tuples

        Returns:
            True if overlap detected
        """
        for used_start, used_end in used_ranges:
            # No overlap if: end <= used_start OR start >= used_end
            # Overlap if: NOT (end <= used_start OR start >= used_end)
            if not (end <= used_start or start >= used_end):
                return True
        return False
