"""
DataCoverageReport - Data Continuity Analysis
Validates time range coverage and detects gaps in tick data

Gap detection and human-readable coverage reports
"""

from typing import Dict, List

import pandas as pd

from python.configuration.discoveries_config_loader import DiscoveriesConfigLoader
from python.configuration.import_config_manager import ImportConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.discoveries.data_coverage.data_format_version_spans import build_version_spans
from python.framework.discoveries.data_coverage.gap_file_attribution import (
    ROLLOVER_TOLERANCE_S,
    attribute_gaps_to_files,
)
from python.framework.types.coverage_report_types import Gap
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.utils.market_calendar import GapCategory, MarketCalendar
from python.framework.utils.time_utils import ensure_utc_aware, format_duration
from python.framework.utils.timeframe_config_utils import TimeframeConfig

# Maximum version spans listed before the rest is summarized
MAX_VERSION_SPANS_DISPLAYED = 20

# From a full day up, hours stop being readable and the calendar-day figure is added
DAY_SCALE_THRESHOLD_H = 24

# Mon-Fri — the unit a gap is measured in on a market that closes
TRADING_DAYS_PER_WEEK = 5


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

        Gaps are detected from the bar sequence. Which file(s) a gap falls in is
        NOT resolved here — that needs the tick index, and the batch validation
        path must not pay for the load. It happens on the render path instead.

        Args:
            config: Optional data coverage config with:
                - data_coverage.enabled (bool)
                - data_coverage.granularity (str, default 'M5')
                - data_coverage.thresholds.short (float, default 0.5)
                - data_coverage.thresholds.moderate (float, default 4.0)
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
        gap_config = config.get('data_coverage', {})
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
                # Split gap at market boundaries (weekends) to prevent
                # data loss from being masked as expected closures.
                # Only affects forex gaps > 80h spanning multiple weekends.
                if self._weekend_closure:
                    sub_gaps = MarketCalendar.split_gap_at_market_boundaries(
                        prev_ts, curr_ts
                    )
                else:
                    sub_gaps = [(prev_ts, curr_ts)]

                for seg_start, seg_end in sub_gaps:
                    seg_seconds = (seg_end - seg_start).total_seconds()

                    category, reason = MarketCalendar.classify_gap(
                        seg_start,
                        seg_end,
                        seg_seconds,
                        thresholds,
                        weekend_closure=self._weekend_closure
                    )

                    gap = Gap(
                        gap_seconds=seg_seconds,
                        category=category,
                        reason=f'{reason} [detected via {granularity}]',
                        gap_start=seg_start,
                        gap_end=seg_end
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
                'Short gaps detected - likely MT5 restarts or connection blips (usually harmless)'
            )

        if self.gap_counts['moderate'] > 0:
            recommendations.append(
                'Check MQL5 TickCollector logs for moderate gaps'
            )
            recommendations.append(
                'Verify broker connection stability during gap periods'
            )

        if self.gap_counts['large'] > 0:
            recommendations.append(
                '🔴 Large gaps detected - consider re-collecting data for these periods'
            )
            recommendations.append(
                'Check if MQL5 TickCollector was stopped intentionally'
            )
            recommendations.append(
                'Review broker connection logs for extended outages'
            )

        return recommendations

    def _version_spans_section(self) -> List[str]:
        """
        Build the data format version section from the tick index.

        Render path only — called from generate_report(), never from analyze(). The batch
        validation path does not read the spans and must not pay for the index load.

        Returns:
            Report lines for the section
        """
        tick_index = TickIndexManager()
        tick_index.build_index()
        entries = tick_index.get_symbol_entries(self.broker_type, self.symbol)

        lines = [f"\n{'─'*60}", 'DATA FORMAT VERSION:', f"{'─'*60}"]

        spans = build_version_spans(entries)

        if not spans:
            lines.append('   (no tick index entries for this symbol)')
            return lines

        for span in spans[:MAX_VERSION_SPANS_DISPLAYED]:
            file_label = 'file' if span.file_count == 1 else 'files'
            files = f'{span.file_count:>5} {file_label},'
            lines.append(
                f"   {span.version:<7} "
                f"{span.start_time.strftime('%Y-%m-%d %H:%M')} → "
                f"{span.end_time.strftime('%Y-%m-%d %H:%M')} "
                f"{files:<13} "
                f"{span.tick_count:>12,} ticks"
            )

        if len(spans) > MAX_VERSION_SPANS_DISPLAYED:
            lines.append(
                f'   ... and {len(spans) - MAX_VERSION_SPANS_DISPLAYED} more spans')

        return lines

    def _attribute_gaps(self) -> None:
        """
        Resolve which file(s) each gap falls in, from the tick index.

        Render path only, for the same reason as the version spans: the batch
        validation path never reads the attribution and must not pay for the
        index load. No data file is opened.
        """
        tick_index = TickIndexManager()
        tick_index.build_index()
        entries = tick_index.get_symbol_entries(self.broker_type, self.symbol)

        offset_hours = ImportConfigManager().get_default_offset(self.broker_type)
        attribute_gaps_to_files(self.gaps, entries, offset_hours)

    def _gap_extent(self, gap: Gap) -> str:
        """
        Render a gap's length, adding calendar days once hours stop being readable.

        Args:
            gap: The gap to describe

        Returns:
            Length string for the report
        """
        extent = f'{gap.duration_human} ({gap.gap_hours:.2f}h'
        if gap.gap_hours >= DAY_SCALE_THRESHOLD_H:
            extent += f' · {gap.gap_hours / 24:.1f}d'
        return extent + ')'

    def _trading_time_lost(self, gap: Gap) -> str:
        """
        Express a gap as trading time lost — the figure that matters for a market
        that closes.

        Only for markets with a weekend closure: a 24/7 market has no trading
        week, so calendar days already are the whole truth there (§37 — the gate
        belongs in the caller, not in MarketCalendar). Only from a full day up,
        because the trading-day count is calendar-day based and would read a
        two-hour Wednesday gap as "1 trading day".

        Args:
            gap: The gap to measure

        Returns:
            Description of the lost trading time, or an empty string when the
            figure would not be meaningful
        """
        if not self._weekend_closure:
            return ''
        if gap.gap_hours < DAY_SCALE_THRESHOLD_H:
            return ''
        if gap.gap_start is None or gap.gap_end is None:
            return ''

        days = MarketCalendar.get_trading_days(gap.gap_start, gap.gap_end)
        if days < 1:
            return ''

        label = f"{days} trading day{'s' if days != 1 else ''}"

        weeks, remainder = divmod(days, TRADING_DAYS_PER_WEEK)
        if remainder == 0 and weeks >= 1:
            plural = 's' if weeks > 1 else ''
            return f'{label} ({weeks} full trading week{plural})'
        if days > TRADING_DAYS_PER_WEEK:
            return f'{label} ({days / TRADING_DAYS_PER_WEEK:.1f} trading weeks)'
        return label

    def _gap_location(self, gap: Gap) -> str:
        """
        Describe where a gap sits relative to the collector's files.

        States the observation, never the conclusion: a boundary reports how long
        after the gap started collection resumed, and whether that was the file
        rolling over. Whether a pause was the venue or the operator is a judgment
        and stays with the reader.

        Args:
            gap: The gap to describe

        Returns:
            One-line description for the report
        """
        if gap.file_before is None or gap.file_after is None:
            return 'unattributed (no tick index entry)'

        if gap.file_before == gap.file_after:
            return f'Intra-file — {gap.file_before} (collector was running)'

        transition = f'File boundary — {gap.file_before} → {gap.file_after}'

        resumed = gap.next_file_opened_after_s
        if resumed is None:
            return transition

        if abs(resumed) < ROLLOVER_TOLERANCE_S:
            return f'{transition} (rollover, collection continued)'

        return f'{transition} (collection resumed {format_duration(resumed)} later)'

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
            f'📊 DATA COVERAGE REPORT: {self.broker_type}/{self.symbol}')
        report.append(f"{'='*60}")

        market_config = MarketConfigManager()
        market_type = market_config.get_market_type(self.broker_type)
        report.append(
            f'Market Type:  {market_type.value}')
        report.append(
            f"Time Range:   {self.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        report.append(
            f"           → {self.end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        duration = self.end_time - self.start_time
        duration_days = duration.days
        duration_hours = duration.total_seconds() / 3600
        report.append(
            f'Duration:     {duration_days}d {int(duration_hours % 24)}h')

        # === SECTION 1b: Data Format Version ===
        report.extend(self._version_spans_section())

        # === SECTION 2: Gap Summary ===
        report.append(f"\n{'─'*60}")
        report.append('GAP ANALYSIS:')
        report.append(f"{'─'*60}")
        report.append(
            f"✅ Seamless:     {self.gap_counts['seamless']} transitions")
        if self._weekend_closure:
            report.append(
                f"✅ Weekend:      {self.gap_counts['weekend']} gaps (expected)")
            report.append(
                f"✅ Holiday:      {self.gap_counts['holiday']} gaps (expected)")
        else:
            report.append(
                '   Weekend:      n/a (24/7 market)')
            report.append(
                '   Holiday:      n/a (24/7 market)')
        report.append(
            f"⚠️  Short:        {self.gap_counts['short']} gaps (< 30 min)")
        report.append(
            f"⚠️  Moderate:     {self.gap_counts['moderate']} gaps (30min - 4h)")
        report.append(
            f"🔴 Large:        {self.gap_counts['large']} gaps (> 4h)")

        # === SECTION 3: Weekend Gaps (compact summary) ===
        weekend_gaps = [
            g for g in self.gaps if g.category == GapCategory.WEEKEND]

        if weekend_gaps:
            total_hours = sum(g.gap_hours for g in weekend_gaps)
            first_start = weekend_gaps[0].gap_start.strftime('%Y-%m-%d')
            last_end = weekend_gaps[-1].gap_end.strftime('%Y-%m-%d')
            report.append(f"\n{'─'*60}")
            report.append(
                f'✅ WEEKEND GAPS: {len(weekend_gaps)} closures, '
                f'{total_hours:.0f}h total ({first_start} → {last_end})'
            )

        # === SECTION 3b: Holiday Gaps (compact summary) ===
        holiday_gaps = [
            g for g in self.gaps if g.category == GapCategory.HOLIDAY]

        if holiday_gaps:
            holiday_dates = [
                g.gap_start.strftime('%Y-%m-%d') for g in holiday_gaps]
            report.append(
                f"✅ HOLIDAY GAPS: {len(holiday_gaps)} closures "
                f"({', '.join(holiday_dates)})"
            )

        # === SECTION 4: Detailed Gap List ===
        problematic_gaps = [g for g in self.gaps if g.category in [
            GapCategory.MODERATE, GapCategory.LARGE]]

        if problematic_gaps:
            report.append(f"\n{'─'*60}")
            report.append('⚠️  GAP DETAILS:')
            report.append(f"{'─'*60}")

            self._attribute_gaps()

            for gap in problematic_gaps:
                report.append(
                    f'\n{gap.severity_icon} {gap.category.value.upper()} GAP:')

                report.append(f'   Type:   {self._gap_location(gap)}')
                report.append(
                    f"   Start:  {gap.gap_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                report.append(
                    f"   End:    {gap.gap_end.strftime('%Y-%m-%d %H:%M:%S')} UTC")

                report.append(f'   Gap:    {self._gap_extent(gap)}')

                trading_loss = self._trading_time_lost(gap)
                if trading_loss:
                    report.append(f'   Lost:   {trading_loss}')

                report.append(f'   Reason: {gap.reason}')

        # Show short gaps if present (compact format, capped display)
        short_gaps = [g for g in self.gaps if g.category == GapCategory.SHORT]
        if short_gaps:
            max_display = 20
            report.append(f"\n{'─'*60}")
            report.append(
                f'ℹ️  SHORT GAPS (< 30 min): {len(short_gaps)} total')
            report.append(f"{'─'*60}")
            for gap in short_gaps[:max_display]:
                report.append(
                    f"   {gap.gap_start.strftime('%Y-%m-%d %H:%M')} → "
                    f"{gap.gap_end.strftime('%H:%M')} ({gap.duration_human})"
                )
            if len(short_gaps) > max_display:
                report.append(
                    f'   ... and {len(short_gaps) - max_display} more short gaps'
                )

        # === SECTION 5: Recommendations ===
        recommendations = self.get_recommendations()

        if recommendations:
            report.append(f"\n{'─'*60}")
            report.append('💡 RECOMMENDATIONS:')
            report.append(f"{'─'*60}")
            for rec in recommendations:
                report.append(f'   • {rec}')
        else:
            report.append(
                '\n✅ All files form continuous timeline - no action needed!')

        report.append(f"{'='*60}\n")

        return '\n'.join(report)
