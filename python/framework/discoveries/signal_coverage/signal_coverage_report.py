"""
SignalCoverageReport - Signal Continuity Analysis

The signal-source counterpart of DataCoverageReport: detects gaps in a signal
series (#429) and classifies them with the shared Gap machinery.

Three deliberate differences to the tick report:
- Keyed by (data_sentiment_type, symbol) — a signal source has no broker.
- weekend_closure is always False. The producing engine runs 24/7 regardless of
  the traded market, so a weekend hole is a real outage, never an expected
  closure. Passing the market's weekend rule here would absolve exactly the
  outages this report exists to surface.
- The cadence is MEASURED from the series (median snapshot distance) instead of
  configured. A signal series is an eval-cadence grid with processing jitter, so
  the measurement is both more robust and more honest than a configured value.

Coverage is envelope-level: a snapshot exists whenever the source produced an
envelope. An envelope carrying no result for the symbol (partial/error) is a
DEGRADED snapshot, not a gap — that distinction is the runtime resolution's
concern (basis / status / is_stale), not the timeline's.
"""

from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional

import pandas as pd

from python.configuration.discoveries_config_loader import DiscoveriesConfigLoader
from python.framework.types.coverage_report_types import Gap, GapCategory
from python.framework.types.signal_data_types import SignalParquetColumn
from python.framework.utils.market_calendar import MarketCalendar
from python.framework.utils.time_utils import format_duration

# Signal sources are produced by an always-on engine — no market closure applies.
SIGNAL_WEEKEND_CLOSURE = False

# Fallback cadence when a series is too short to measure (one snapshot or none).
DEFAULT_CADENCE_SECONDS = 600.0


class SignalCoverageReport:
    """
    Analyzes signal-series continuity and generates reports.

    Features:
    - Gap detection between consecutive snapshots
    - Measured cadence instead of a configured interval
    - Human-readable reports
    - Window-scoped queries for scenario validation
    """

    def __init__(self, data_sentiment_type: str, symbol: str):
        """
        Initialize signal coverage report.

        Args:
            data_sentiment_type: Signal source identity (= the archive's pipeline_id)
            symbol: Trading symbol the series is scoped to
        """
        self.data_sentiment_type = data_sentiment_type
        self.symbol = symbol
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

        # Measured snapshot cadence (median distance between consecutive snapshots)
        self.cadence_seconds: float = DEFAULT_CADENCE_SECONDS
        self.snapshot_count: int = 0

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

        self._snapshots: List[datetime] = []

    def analyze(self, paths: List[Path]) -> None:
        """
        Analyze the signal parquet files for continuity and gaps.

        Reads only the collected_msc column (projection) — the timeline is all
        this report needs. Paths are resolved by the caller from the signal
        index, which already scopes files to the symbol.

        Args:
            paths: Signal parquet files covering this (source, symbol)
        """
        self._snapshots = self._load_snapshot_times(paths)
        self.snapshot_count = len(self._snapshots)

        # Early exit: nothing to analyze
        if not self._snapshots:
            return

        self.start_time = self._snapshots[0]
        self.end_time = self._snapshots[-1]

        if self.snapshot_count < 2:
            return

        self.cadence_seconds = self._measure_cadence(self._snapshots)
        thresholds = self._load_thresholds()

        # A gap exists when the distance exceeds 2x the measured cadence — the
        # same tolerance the tick report applies to its bar interval. Breaking
        # wakes SHORTEN a distance, so they never trigger a false positive.
        gap_threshold_s = self.cadence_seconds * 2

        for i in range(1, len(self._snapshots)):
            prev_ts = self._snapshots[i - 1]
            curr_ts = self._snapshots[i]
            delta_s = (curr_ts - prev_ts).total_seconds()

            if delta_s <= gap_threshold_s:
                continue

            category, reason = MarketCalendar.classify_gap(
                prev_ts,
                curr_ts,
                delta_s,
                thresholds,
                weekend_closure=SIGNAL_WEEKEND_CLOSURE
            )

            missed = self._missed_snapshots(delta_s)
            gap = Gap(
                gap_seconds=delta_s,
                category=category,
                reason=f"{reason} [~{missed} snapshots missed]",
                gap_start=prev_ts,
                gap_end=curr_ts
            )
            self.gaps.append(gap)
            self.gap_counts[category.value] += 1

    def _load_snapshot_times(self, paths: List[Path]) -> List[datetime]:
        """
        Read the distinct snapshot timestamps from the signal parquet files.

        Args:
            paths: Signal parquet files to read

        Returns:
            Ascending, de-duplicated snapshot times (UTC-aware)
        """
        column = SignalParquetColumn.COLLECTED_MSC.value
        frames = [pd.read_parquet(path, columns=[column]) for path in paths]
        if not frames:
            return []

        df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        msc_values = sorted(set(int(v) for v in df[column]))
        return [
            datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc)
            for msc in msc_values
        ]

    def _measure_cadence(self, snapshots: List[datetime]) -> float:
        """
        Measure the series cadence as the median distance between snapshots.

        The median is robust against both the eval-cadence jitter and the
        off-grid breaking wakes, and it survives a producer-side config change
        that a configured interval would not.

        Args:
            snapshots: Ascending snapshot times

        Returns:
            Median distance in seconds (never zero)
        """
        deltas = [
            (snapshots[i] - snapshots[i - 1]).total_seconds()
            for i in range(1, len(snapshots))
        ]
        measured = median(deltas)
        return measured if measured > 0 else DEFAULT_CADENCE_SECONDS

    def _missed_snapshots(self, gap_seconds: float) -> int:
        """
        How many snapshots the gap swallowed at the measured cadence.

        Args:
            gap_seconds: Gap duration in seconds

        Returns:
            Estimated number of missing snapshots
        """
        return max(0, int(gap_seconds / self.cadence_seconds) - 1)

    def _load_thresholds(self) -> Dict[str, float]:
        """
        Load the signal gap thresholds (hours) from the discoveries config.

        Returns:
            Threshold dict for MarketCalendar.classify_gap
        """
        config = DiscoveriesConfigLoader().get_config_raw()
        signal_config = config.get('signal_coverage', {})
        return signal_config.get('thresholds', {'short': 0.5, 'moderate': 1.0})

    # =========================================================================
    # WINDOW QUERIES (scenario validation)
    # =========================================================================

    def has_snapshot_at_or_before(self, moment: datetime) -> bool:
        """
        Whether a snapshot exists at or before a moment.

        The signal analogue of warmup: a SIGNAL worker resolves the nearest
        snapshot at or before the tick, so without one the run starts blind.

        Args:
            moment: Reference moment (UTC-aware)

        Returns:
            True if at least one snapshot is at or before the moment
        """
        return bool(self._snapshots) and self._snapshots[0] <= moment

    def latest_snapshot_at_or_before(self, moment: datetime) -> Optional[datetime]:
        """
        The newest snapshot at or before a moment — what the worker would resolve.

        Args:
            moment: Reference moment (UTC-aware)

        Returns:
            Snapshot time, or None when the moment precedes the series
        """
        latest = None
        for snapshot in self._snapshots:
            if snapshot > moment:
                break
            latest = snapshot
        return latest

    def gaps_in_window(self, start: datetime, end: datetime) -> List[Gap]:
        """
        Gaps fully contained in a window.

        Args:
            start: Window start (UTC-aware)
            end: Window end (UTC-aware)

        Returns:
            Gaps whose start and end both fall inside the window
        """
        return [
            gap for gap in self.gaps
            if gap.gap_start >= start and gap.gap_end <= end
        ]

    def coverage_ratio_in_window(self, start: datetime, end: datetime) -> float:
        """
        Share of a window NOT swallowed by a gap.

        Args:
            start: Window start (UTC-aware)
            end: Window end (UTC-aware)

        Returns:
            Ratio 0.0–1.0 (1.0 when no gap falls inside the window)
        """
        span_s = (end - start).total_seconds()
        if span_s <= 0:
            return 1.0

        gap_s = sum(gap.gap_seconds for gap in self.gaps_in_window(start, end))
        return max(0.0, 1.0 - gap_s / span_s)

    def has_issues(self) -> bool:
        """
        Check if there are any problematic gaps.

        Returns:
            True if moderate or large gaps exist
        """
        return self.gap_counts['moderate'] + self.gap_counts['large'] > 0

    # =========================================================================
    # REPORT
    # =========================================================================

    def generate_report(self) -> str:
        """
        Generate human-readable signal coverage report.

        Returns:
            Formatted report string
        """
        report = []

        report.append(f"\n{'='*60}")
        report.append(
            f"📡 SIGNAL COVERAGE REPORT: {self.data_sentiment_type}/{self.symbol}")
        report.append(f"{'='*60}")

        if not self.snapshot_count:
            report.append("   (no snapshots — source or symbol not imported)")
            report.append(f"{'='*60}\n")
            return "\n".join(report)

        report.append(
            f"Time Range:   {self.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        report.append(
            f"           → {self.end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        duration = self.end_time - self.start_time
        report.append(
            f"Duration:     {duration.days}d {int(duration.total_seconds() / 3600 % 24)}h")
        report.append(f"Snapshots:    {self.snapshot_count:,}")
        report.append(
            f"Cadence:      {format_duration(self.cadence_seconds)} (measured median)")

        report.append(f"\n{'─'*60}")
        report.append("GAP ANALYSIS:")
        report.append(f"{'─'*60}")
        report.append(
            f"⚠️  Short:        {self.gap_counts['short']} gaps")
        report.append(
            f"⚠️  Moderate:     {self.gap_counts['moderate']} gaps")
        report.append(
            f"🔴 Large:        {self.gap_counts['large']} gaps")

        problematic = [
            g for g in self.gaps
            if g.category in (GapCategory.MODERATE, GapCategory.LARGE)
        ]

        if problematic:
            report.append(f"\n{'─'*60}")
            report.append("⚠️  GAP DETAILS:")
            report.append(f"{'─'*60}")
            for gap in problematic:
                report.append(
                    f"\n{gap.severity_icon} {gap.category.value.upper()} GAP:")
                report.append(
                    f"   Start:  {gap.gap_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                report.append(
                    f"   End:    {gap.gap_end.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                report.append(
                    f"   Gap:    {gap.duration_human} ({gap.gap_hours:.2f}h)")
                report.append(f"   Reason: {gap.reason}")

        short_gaps = [g for g in self.gaps if g.category == GapCategory.SHORT]
        if short_gaps:
            max_display = 20
            report.append(f"\n{'─'*60}")
            report.append(f"ℹ️  SHORT GAPS: {len(short_gaps)} total")
            report.append(f"{'─'*60}")
            for gap in short_gaps[:max_display]:
                report.append(
                    f"   {gap.gap_start.strftime('%Y-%m-%d %H:%M')} → "
                    f"{gap.gap_end.strftime('%H:%M')} ({gap.duration_human})"
                )
            if len(short_gaps) > max_display:
                report.append(
                    f"   ... and {len(short_gaps) - max_display} more short gaps")

        if not self.gaps:
            report.append("\n✅ Continuous signal series - no gaps detected!")

        report.append(f"{'='*60}\n")

        return "\n".join(report)
