"""
DataCoverageReport - Data Continuity Analysis
Validates time range coverage and detects gaps in tick data

Gap detection and human-readable coverage reports
"""

from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from python.configuration.discoveries_config_loader import DiscoveriesConfigLoader
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.discoveries.data_coverage.data_format_version_spans import build_version_spans
from python.framework.discoveries.data_coverage.gap_file_attribution import (
    ROLLOVER_TOLERANCE_S,
    attribute_gaps_to_files,
)
from python.framework.discoveries.data_coverage.gap_listing import (
    MISSING_DATA_CATEGORIES,
    gap_span,
    recent_gap_listing,
)
from python.framework.discoveries.data_coverage.gap_timeline import GapTimeline
from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.types.coverage_report_types import Gap, GapYearOriginSummary
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

# The broker types whose collector is the MQL5 TickCollector — the only ones its advice is for
_MT5_BROKER_VALUES = {
    BrokerType.MT5_FOREX.value, BrokerType.MT5_CFD.value, BrokerType.MT5_CRYPTO.value}

# What a gap no tick file could be found for is called where an origin class would stand
UNATTRIBUTED_LABEL = 'unattributed'


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
        """
        self.symbol = symbol
        self.broker_type = broker_type
        # Set by analyze() from the bar file; both stay None where the symbol has no bars
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

        # Get market rules for weekend closure detection
        market_config = MarketConfigManager()
        market_rules = market_config.get_market_rules_for_broker(broker_type)
        self._weekend_closure = market_rules.weekend_closure

        # Whether the gaps carry their files and origin classes yet (render path only)
        self._gaps_attributed = False
        # This symbol's tick index entries, read on first use (render path only)
        self._tick_index_entries: Optional[List[Dict]] = None
        # The gaps in start order for window queries, built on first use
        self._gap_timeline: Optional[GapTimeline] = None

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
        granularity = self.detection_granularity()
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
        timestamps = pd.to_datetime(bars_df['timestamp'], utc=True)

        # Detect gaps via timestamp jumps between consecutive bars
        # A gap exists when the interval exceeds 2x the expected bar interval
        # (allows for minor timing jitter without false positives)
        gap_threshold_s = expected_interval_s * 2

        # The jumps are found over the whole column at once, and only they reach the loop: a
        # loop over every bar cost 110 s for a symbol with years of minute bars (measured
        # 2026-10-15, 2.4 million bars), nearly all of it reading one row at a time
        deltas_s = timestamps.diff().dt.total_seconds().to_numpy()
        for i in np.flatnonzero(deltas_s > gap_threshold_s):
            prev_ts = timestamps.iloc[i - 1]
            curr_ts = timestamps.iloc[i]

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
        Whether our own collection has moderate or large gaps.

        A gap in a venue's published history is the venue's silence, and one no tick file was
        found for is not ours to judge (`unjudged_gap_count`). Render path: the gaps are
        attributed first.

        Returns:
            True if our collection has moderate or large gaps
        """
        collected = self.collected_gap_counts()
        return collected[GapCategory.MODERATE] + collected[GapCategory.LARGE] > 0

    def collected_gap_counts(self) -> Counter:
        """
        The gaps of our own collection, counted per category. Render path: attributes first.

        Returns:
            GapCategory → count
        """
        self.attribute_gaps()
        return Counter(gap.category for gap in self.gaps if _is_collected(gap))

    def unjudged_gap_count(self) -> int:
        """
        The moderate and large gaps no tick file was found for — whose they are is unknown.

        Returns:
            Their count
        """
        self.attribute_gaps()
        return sum(1 for gap in self.gaps
                   if gap.origin_class is None
                   and gap.category in (GapCategory.MODERATE, GapCategory.LARGE))

    def gaps_in_window(self, start: datetime, end: datetime) -> List[Gap]:
        """
        Gaps fully contained in a window — the question a run's validation asks per scenario.

        Args:
            start: Window start (UTC-aware)
            end: Window end (UTC-aware)

        Returns:
            The gaps whose start and end both fall inside the window, oldest first
        """
        return self._timeline().gaps_in_window(start, end)

    def gap_containing(self, moment: datetime) -> Optional[Gap]:
        """
        The gap a moment lies strictly inside, if any.

        Args:
            moment: The instant to locate (UTC-aware)

        Returns:
            The gap with gap_start < moment < gap_end, or None
        """
        return self._timeline().gap_containing(moment)

    def _timeline(self) -> GapTimeline:
        """
        The gaps in start order, built on first use and again whenever the gap list changed.

        Returns:
            The timeline over the current gap list
        """
        if self._gap_timeline is None or not self._gap_timeline.covers(self.gaps):
            self._gap_timeline = GapTimeline(self.gaps)
        return self._gap_timeline

    def get_recommendations(self) -> List[str]:
        """
        Generate actionable recommendations for the gaps of our OWN collection.

        A gap in a venue's published history (`external`) is the venue's own silence: there is
        nothing to re-collect and nothing to recommend. A gap no tick file could be found for is
        not ours to judge either. The MT5 advice names the MQL5 TickCollector, so it is given
        for an MT5 broker only. Render path: the gaps are attributed first.

        Returns:
            List of recommendation strings
        """
        collected = self.collected_gap_counts()
        mt5 = self._broker_value() in _MT5_BROKER_VALUES

        recommendations = []

        if collected[GapCategory.SHORT] > 0:
            recommendations.append(
                'Short gaps in our collection - likely MT5 restarts or connection blips '
                '(usually harmless)' if mt5 else
                'Short gaps in our collection - likely collector reconnects (usually harmless)'
            )

        if collected[GapCategory.MODERATE] > 0:
            if mt5:
                recommendations.append(
                    'Check MQL5 TickCollector logs for moderate gaps'
                )
                recommendations.append(
                    'Verify broker connection stability during gap periods'
                )
            else:
                recommendations.append(
                    "Check the collector's logs for the moderate gaps listed above"
                )

        if collected[GapCategory.LARGE] > 0:
            recommendations.append(
                '🔴 Large gaps in our collection - consider re-collecting data for these periods'
            )
            if mt5:
                recommendations.append(
                    'Check if MQL5 TickCollector was stopped intentionally'
                )
                recommendations.append(
                    'Review broker connection logs for extended outages'
                )
            else:
                recommendations.append(
                    "Check whether the collector was stopped, and the venue's status for those hours"
                )

        return recommendations

    def summary_by_year_and_origin(self) -> List[GapYearOriginSummary]:
        """
        The gaps that mean missing data, counted per year and data origin.

        Computed here rather than in the printout, so every surface reads the same figures.
        Render path: the gaps are attributed first, which reads the tick index once.

        Returns:
            One row per year and origin class: oldest year first, within a year the origin
            classes in their declared order and the unattributed gaps last
        """
        self.attribute_gaps()
        rows: Dict[Tuple[int, Optional[OriginClass]], GapYearOriginSummary] = {}
        for gap in self.gaps:
            if gap.category not in MISSING_DATA_CATEGORIES or gap.gap_start is None:
                continue
            key = (ensure_utc_aware(gap.gap_start).year, gap.origin_class)
            row = rows.get(key)
            if row is None:
                row = GapYearOriginSummary(
                    year=key[0], origin_class=key[1],
                    short=0, moderate=0, large=0, hours_without_data=0.0)
                rows[key] = row
            if gap.category == GapCategory.SHORT:
                row.short += 1
            elif gap.category == GapCategory.MODERATE:
                row.moderate += 1
            else:
                row.large += 1
            row.hours_without_data += gap.gap_hours

        declared = list(OriginClass)
        return sorted(
            rows.values(),
            key=lambda row: (row.year, declared.index(row.origin_class)
                             if row.origin_class is not None else len(declared))
        )

    def _broker_value(self) -> str:
        """
        The broker type as its string value — the report is built from either form.

        Returns:
            The broker type's value, e.g. 'mt5' or 'kraken_spot'
        """
        if isinstance(self.broker_type, BrokerType):
            return self.broker_type.value
        return str(self.broker_type)

    def _version_spans_section(self) -> List[str]:
        """
        Build the data format version section from the tick index.

        Render path only — called from generate_report(), never from analyze(). The batch
        validation path does not read the spans and must not pay for the index load.

        Returns:
            Report lines for the section
        """
        lines = [f"\n{'─'*60}", 'DATA FORMAT VERSION:', f"{'─'*60}"]

        spans = build_version_spans(self._tick_entries())

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

    def attribute_gaps(self) -> None:
        """
        Resolve which file(s) each gap falls in, and whose data they are, from the tick index.

        Render path only, for the same reason as the version spans: the batch
        validation path never reads the attribution and must not pay for the
        index load. No data file is opened. Runs once per report — the listing, the
        summary, the recommendations and the API's gap route all read it.
        """
        if self._gaps_attributed:
            return
        self._gaps_attributed = True

        server_clock = MarketConfigManager().get_server_clock(self.broker_type)
        granularity = self.detection_granularity()
        bar_interval = timedelta(minutes=TimeframeConfig.get_minutes(granularity))
        attribute_gaps_to_files(self.gaps, self._tick_entries(), server_clock, bar_interval)

    @staticmethod
    def detection_granularity() -> str:
        """
        The bar timeframe gaps are detected on, as the discoveries config sets it.

        A cached report was detected on the same one: the cache is invalid once it changes.

        Returns:
            The timeframe, e.g. 'M1'
        """
        return DiscoveriesConfigLoader().get_config_raw().get(
            'data_coverage', {}).get('granularity', 'M5')

    def _tick_entries(self) -> List[Dict]:
        """
        This symbol's tick index entries, read once per report.

        Render path only: the version spans and the attribution both read them. Loaded, never
        built: the render path includes an API route, and a build scans every tick file and
        rewrites the index — that belongs to the import and to the batch. An index that cannot
        be read leaves the gaps unattributed, and the report says they were not judged.

        Returns:
            The entries, chronologically sorted
        """
        if self._tick_index_entries is None:
            tick_index = TickIndexManager()
            tick_index.load_index()
            self._tick_index_entries = tick_index.get_symbol_entries(
                self.broker_type, self.symbol)
        return self._tick_index_entries

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
        Describe where a gap sits and whose silence it is.

        The data's origin decides the reading. In a venue's published history (`external`)
        no collector of ours was running: a gap there is a stretch in which nobody traded at
        the venue, and there is nothing to re-collect. Only in a file our collector wrote does
        the file layout say something about the collector — and then it states the
        observation, never the conclusion: a boundary reports how long after the gap started
        collection resumed, and whether that was the file rolling over. Whether a pause was
        the venue or the operator is a judgment and stays with the reader.

        Args:
            gap: The gap to describe

        Returns:
            One-line description for the report
        """
        if gap.file_before is None or gap.file_after is None:
            return 'unattributed (no tick index entry)'

        if gap.is_origin_seam:
            return (f'where the {gap.origin_class_before.value} data ends and the '
                    f'{gap.origin_class_after.value} data begins')

        if gap.origin_class == OriginClass.EXTERNAL:
            if gap.file_before == gap.file_after:
                return f'inside {gap.file_before} — no trades at the venue, nothing to re-collect'
            return "between two files of the venue's history — no trades at the venue"

        if gap.file_before == gap.file_after:
            return f'inside {gap.file_before}, the collector was running'

        resumed = gap.next_file_opened_after_s
        if resumed is None:
            return 'between two files'

        if abs(resumed) < ROLLOVER_TOLERANCE_S:
            return 'between two files, at a rollover — collection continued'

        return f'between two files, collection resumed {format_duration(resumed)} later'

    def _gap_line(self, gap: Gap) -> str:
        """
        One gap on one line: when, how long, where, and whose data it lies in.

        Args:
            gap: The gap to describe

        Returns:
            The report line
        """
        line = f'   {gap_span(gap)}{self._gap_extent(gap):<24}  {self._gap_location(gap)}'

        trading_loss = self._trading_time_lost(gap)
        if trading_loss:
            line += f' · lost {trading_loss}'

        return f'{line}   [{_origin_label(gap.origin_class)}]'

    def _summary_section(self) -> List[str]:
        """
        Render the yearly summary by data origin.

        Returns:
            Report lines for the section
        """
        lines = [
            f"\n{'─'*60}",
            'BY YEAR AND ORIGIN (short · moderate · large, and the hours without data):',
            f"{'─'*60}",
            f"   {'year':<6}{'origin':<14}{'short':>9}{'moderate':>10}{'large':>8}{'hours':>12}",
        ]
        for row in self.summary_by_year_and_origin():
            lines.append(
                f'   {row.year:<6}{_origin_label(row.origin_class):<14}'
                f'{row.short:>9,}{row.moderate:>10,}{row.large:>8,}'
                f'{row.hours_without_data:>12,.1f}'
            )
        return lines

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

        if self.start_time is None or self.end_time is None:
            # No bar file for this symbol, or an empty one — a mistyped symbol, or bars never
            # rendered. Said as such rather than failing on a range that does not exist (#586).
            report.append('(no bar data for this symbol)')
            report.append(f"{'='*60}\n")
            return '\n'.join(report)

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

        # === SECTION 4: The most recent gaps of each category ===
        # A decade of a venue's history holds tens of thousands of gaps; the report lists the
        # newest of each category and counts the rest by year and origin below.
        missing = [g for g in self.gaps if g.category in MISSING_DATA_CATEGORIES]

        if missing:
            self.attribute_gaps()

            report.append(f"\n{'─'*60}")
            report.append('GAPS — the most recent of each category, newest first:')
            report.append(f"{'─'*60}")
            report.extend(recent_gap_listing(missing, self._gap_line))

            # === SECTION 4b: By year and origin ===
            report.extend(self._summary_section())

        # === SECTION 5: Recommendations ===
        recommendations = self.get_recommendations()

        if recommendations:
            report.append(f"\n{'─'*60}")
            report.append('💡 RECOMMENDATIONS (our own collection):')
            report.append(f"{'─'*60}")
            for rec in recommendations:
                report.append(f'   • {rec}')
        elif not missing:
            report.append(
                '\n✅ All files form continuous timeline - no action needed!')
        elif any(gap.origin_class is None for gap in missing):
            # No clean bill: a gap no tick file was found for was not judged at all
            report.append(f'\n⚠️  Not judged — {_no_action_reason(missing)}, so whose data '
                          f'they lie in is unknown')
        else:
            report.append(f'\n✅ No gap in our own collection — {_no_action_reason(missing)}')

        report.append(f"{'='*60}\n")

        return '\n'.join(report)


def _is_collected(gap: Gap) -> bool:
    """
    Whether a gap lies in data a collector recorded — ours, as opposed to a venue's published
    history or a stretch no tick file could be found for.

    Args:
        gap: An attributed gap

    Returns:
        True for a gap whose origin class is known and not external
    """
    return gap.origin_class is not None and gap.origin_class != OriginClass.EXTERNAL


def _origin_label(origin_class: Optional[OriginClass]) -> str:
    """
    The word a report line shows for an origin class.

    Args:
        origin_class: The gap's or the summary row's origin class, None when unattributed

    Returns:
        The class value, or the unattributed label
    """
    return origin_class.value if origin_class is not None else UNATTRIBUTED_LABEL


def _no_action_reason(missing: List[Gap]) -> str:
    """
    Say why gaps exist and none of them calls for action — they all lie outside our own
    collection.

    Args:
        missing: The gaps that mean missing data, none of them in collected data

    Returns:
        The reason, counted per kind
    """
    external = sum(1 for gap in missing if gap.origin_class == OriginClass.EXTERNAL)
    unattributed = sum(1 for gap in missing if gap.origin_class is None)
    parts = []
    if external:
        parts.append(f"{external:,} lie in the venue's own history (external): no trades at "
                     f'the venue, nothing to re-collect')
    if unattributed:
        parts.append(f'{unattributed:,} could not be matched to a tick file')
    return '; '.join(parts)
