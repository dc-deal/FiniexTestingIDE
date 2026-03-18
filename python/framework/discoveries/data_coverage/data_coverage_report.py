"""
DataCoverageReport - Data Continuity Analysis
Validates time range coverage and detects gaps in tick data

Gap detection and human-readable coverage reports
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict
from pathlib import Path

import pandas as pd
import pytz

from python.configuration.discoveries_config_loader import DiscoveriesConfigLoader
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.coverage_report_types import Gap, IndexEntry
from python.framework.utils.market_calendar import MarketCalendar, GapCategory
from python.framework.types.market_types.market_types import VALIDATION_TIMEZONE
from python.framework.utils.time_utils import ensure_utc_aware, format_duration
from python.framework.utils.timeframe_config_utils import TimeframeConfig


class DataCoverageReport:
    """
    Analyzes time range coverage and generates reports.

    Features:
    - Gap detection between files
    - Weekend vs. data loss classification
    - Human-readable reports
    - Actionable recommendations
    - Weekend gap listing with Berlin local time
    """

    def __init__(self, symbol: str, broker_type: BrokerType):
        """
        Initialize coverage report.

        Args:
            symbol: Trading symbol
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            data_dir: Data directory for bar access (optional, for intra-file gaps)
        """
        self.symbol = symbol
        self.broker_type = broker_type
        self.start_time = None
        self.end_time = None

        # Get market rules for weekend closure detection
        market_config = MarketConfigManager()
        market_rules = market_config.get_market_rules_for_broker(broker_type)
        self._weekend_closure = market_rules.weekend_closure

        # Analysis results
        self.gaps: List[Gap] = []
        self.gap_counts = {
            'seamless': 0,
            'weekend': 0,
            'holiday': 0,
            'short': 0,
            'moderate': 0,
            'large': 0
        }

    def analyze(self, config: Dict = None) -> None:
        """
        Analyze all files for continuity and gaps.

        Detects both file-to-file gaps and intra-file gaps (via bars).

        Args:
            config: Optional gap detection config with:
                - gap_detection.enabled (bool)
                - gap_detection.granularity (str, default 'M5')
                - gap_detection.thresholds.short (float, default 0.5)
                - gap_detection.thresholds.moderate (float, default 4.0)
        """
        # Detect intra-file gaps if data_dir and config provided
        intra_gaps = self._detect_gaps_from_bars()
        for gap in intra_gaps:
            self.gaps.append(gap)
            self.gap_counts[gap.category.value] += 1

    def _detect_gaps_from_bars(self) -> List[Gap]:
        """
        Detect gaps by finding timestamp jumps between consecutive bars.

        Loads bars at configured granularity and checks the time difference
        between consecutive bars. When the gap exceeds the expected bar
        interval, it is classified via MarketCalendar (weekend, holiday,
        short, moderate, large).

        This approach works regardless of whether synthetic bars exist —
        it detects gaps from timestamp discontinuities in the bar sequence.

        Returns:
            List of Gap objects for detected gaps
        """
        gaps = []

        alysis_config = DiscoveriesConfigLoader()
        config = alysis_config.get_config_raw()

        # Get configuration
        gap_config = config.get('gap_detection', {})
        granularity = gap_config.get('granularity', 'M5')
        thresholds = gap_config.get(
            'thresholds', {'short': 0.5, 'moderate': 4.0})

        # Expected interval between bars (in seconds)
        expected_interval_s = TimeframeConfig.get_minutes(granularity) * 60

        # Initialize bar index
        bar_index = BarsIndexManager()
        bar_index.build_index()

        # Get bar file for symbol
        bar_file = bar_index.get_bar_file(
            self.broker_type, self.symbol, granularity)

        if not bar_file or not bar_file.exists():
            return gaps

        # Load bars
        bars_df = pd.read_parquet(bar_file)

        # fill start and end
        if not (bars_df.empty or len(bars_df) == 0):
            self.start_time = ensure_utc_aware(bars_df.iloc[0]['timestamp'])
            self.end_time = ensure_utc_aware(bars_df.iloc[-1]['timestamp'])

        if len(bars_df) < 2:
            return gaps

        # Ensure timestamps are UTC-aware and sorted
        bars_df = bars_df.sort_values('timestamp').reset_index(drop=True)
        timestamps = bars_df['timestamp'].apply(ensure_utc_aware)

        # Detect gaps via timestamp jumps between consecutive bars
        # A gap exists when the interval exceeds 2x the expected bar interval
        # (allows for minor timing jitter without false positives)
        gap_threshold_s = expected_interval_s * 2

        for i in range(1, len(timestamps)):
            prev_ts = timestamps.iloc[i - 1]
            curr_ts = timestamps.iloc[i]
            delta_s = (curr_ts - prev_ts).total_seconds()

            if delta_s > gap_threshold_s:
                # Gap detected — classify it
                category, reason = MarketCalendar.classify_gap(
                    prev_ts,
                    curr_ts,
                    delta_s,
                    thresholds,
                    weekend_closure=self._weekend_closure
                )

                gap = Gap(
                    gap_seconds=delta_s,
                    category=category,
                    reason=f"{reason} [detected via {granularity}]",
                    gap_start=prev_ts,
                    gap_end=curr_ts
                )

                gaps.append(gap)

        return gaps

    def has_issues(self) -> bool:
        """
        Check if there are any problematic gaps.

        Returns:
            True if moderate or large gaps exist
        """
        return self.gap_counts['moderate'] + self.gap_counts['large'] > 0

    def get_recommendations(self) -> List[str]:
        """
        Generate actionable recommendations based on gaps.

        Returns:
            List of recommendation strings
        """
        recommendations = []

        if self.gap_counts['short'] > 0:
            recommendations.append(
                "Short gaps detected - likely MT5 restarts or connection blips (usually harmless)"
            )

        if self.gap_counts['moderate'] > 0:
            recommendations.append(
                "Check MQL5 TickCollector logs for moderate gaps"
            )
            recommendations.append(
                "Verify broker connection stability during gap periods"
            )

        if self.gap_counts['large'] > 0:
            recommendations.append(
                "🔴 Large gaps detected - consider re-collecting data for these periods"
            )
            recommendations.append(
                "Check if MQL5 TickCollector was stopped intentionally"
            )
            recommendations.append(
                "Review broker connection logs for extended outages"
            )

        return recommendations

    def generate_report(self) -> str:
        """
        Generate human-readable coverage report.

        Returns:
            Formatted report string
        """
        report = []

        # === SECTION 1: Overview ===
        report.append(f"\n{'='*60}")
        report.append(
            f"📊 DATA COVERAGE REPORT: {self.broker_type}/{self.symbol}")
        report.append(f"{'='*60}")

        report.append(
            f"Time Range:   {self.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        report.append(
            f"           → {self.end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        duration = self.end_time - self.start_time
        duration_days = duration.days
        duration_hours = duration.total_seconds() / 3600
        report.append(
            f"Duration:     {duration_days}d {int(duration_hours % 24)}h")

        # === SECTION 2: Gap Summary ===
        report.append(f"\n{'─'*60}")
        report.append("GAP ANALYSIS:")
        report.append(f"{'─'*60}")
        report.append(
            f"✅ Seamless:     {self.gap_counts['seamless']} transitions")
        report.append(
            f"✅ Weekend:      {self.gap_counts['weekend']} gaps (expected)")
        report.append(
            f"✅ Holiday:      {self.gap_counts['holiday']} gaps (expected)")
        report.append(
            f"⚠️  Short:        {self.gap_counts['short']} gaps (< 30 min)")
        report.append(
            f"⚠️  Moderate:     {self.gap_counts['moderate']} gaps (30min - 4h)")
        report.append(
            f"🔴 Large:        {self.gap_counts['large']} gaps (> 4h)")

        # === SECTION 3: Weekend Gaps ===
        weekend_gaps = [
            g for g in self.gaps if g.category == GapCategory.WEEKEND]

        if weekend_gaps:
            report.append(f"\n{'─'*60}")
            report.append("✅ WEEKEND GAPS (Expected Market Closures):")
            report.append(f"{'─'*60}")
            report.append("ℹ️  Expected Market Closure Window:")

            # Get closure window description from MarketCalendar
            closure_desc = MarketCalendar.get_weekend_closure_description()
            for line in closure_desc.split('\n'):
                report.append(f"   {line}")

            # Add timezone validation info
            report.append("")
            report.append("ℹ️  Timezone Validation Settings:")
            report.append(f"   • Validation Timezone: {VALIDATION_TIMEZONE}")

            report.append(f"{'─'*60}")

            gap_counter = 1
            for gap in weekend_gaps:
                # Intra-file gap: use gap_start/gap_end
                utc_start = gap.gap_start.strftime('%Y-%m-%d %H:%M')
                utc_end = gap.gap_end.strftime('%Y-%m-%d %H:%M')

                report.append(
                    f"📅 Weekend Gap #{gap_counter} (intra-file):")
                report.append(f"   Start:  {utc_start} UTC")
                report.append(f"   End:    {utc_end} UTC")
                report.append(f"   Gap:    {gap.gap_hours:.1f} hours")
                report.append(f"   Note:   {gap.reason}")
                report.append("")
                gap_counter += 1
                continue

        # === SECTION 3b: Holiday Gaps ===
        holiday_gaps = [
            g for g in self.gaps if g.category == GapCategory.HOLIDAY]

        if holiday_gaps:
            report.append(f"\n{'─'*60}")
            report.append("✅ HOLIDAY GAPS (Expected Market Closures):")
            report.append(f"{'─'*60}")
            report.append("ℹ️  Known Market Holidays:")
            report.append("   • December 25 (Christmas)")
            report.append("   • January 1 (New Year)")
            report.append(f"{'─'*60}")

            gap_counter = 1
            for gap in holiday_gaps:
                utc_start = gap.gap_start.strftime('%Y-%m-%d %H:%M')
                utc_end = gap.gap_end.strftime('%Y-%m-%d %H:%M')

                report.append(
                    f"🎄 Holiday Gap #{gap_counter} (intra-file):")
                report.append(f"   Start:  {utc_start} UTC")
                report.append(f"   End:    {utc_end} UTC")
                report.append(f"   Gap:    {gap.gap_hours:.1f} hours")
                report.append(f"   Note:   {gap.reason}")
                report.append("")
                gap_counter += 1

        # === SECTION 4: Detailed Gap List ===
        problematic_gaps = [g for g in self.gaps if g.category in [
            GapCategory.MODERATE, GapCategory.LARGE]]

        if problematic_gaps:
            report.append(f"\n{'─'*60}")
            report.append("⚠️  GAP DETAILS:")
            report.append(f"{'─'*60}")

            for gap in problematic_gaps:
                report.append(
                    f"\n{gap.severity_icon} {gap.category.value.upper()} GAP:")

                # Intra-file gap
                report.append(f"   Type:   Intra-file gap")
                report.append(
                    f"   Start:  {gap.gap_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                report.append(
                    f"   End:    {gap.gap_end.strftime('%Y-%m-%d %H:%M:%S')} UTC")

                report.append(
                    f"   Gap:    {gap.duration_human} ({gap.gap_hours:.2f}h)")
                report.append(f"   Reason: {gap.reason}")

        # Show short gaps if present (compact format)
        short_gaps = [g for g in self.gaps if g.category == GapCategory.SHORT]
        if short_gaps:
            report.append(f"\n{'─'*60}")
            report.append("ℹ️  SHORT GAPS (< 30 min):")
            report.append(f"{'─'*60}")
            for gap in short_gaps:
                # Intra-file gap
                report.append(
                    f"   {gap.gap_start.strftime('%Y-%m-%d %H:%M')} → "
                    f"{gap.gap_end.strftime('%H:%M')} ({gap.duration_human}) [intra-file]"
                )

        # === SECTION 5: Recommendations ===
        recommendations = self.get_recommendations()

        if recommendations:
            report.append(f"\n{'─'*60}")
            report.append("💡 RECOMMENDATIONS:")
            report.append(f"{'─'*60}")
            for rec in recommendations:
                report.append(f"   • {rec}")
        else:
            report.append(
                f"\n✅ All files form continuous timeline - no action needed!")

        report.append(f"{'='*60}\n")

        return "\n".join(report)
