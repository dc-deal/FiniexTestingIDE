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
from typing import Dict, List, Optional, Set

import pandas as pd
import pyarrow.parquet as pq

from python.configuration.discoveries_config_loader import DiscoveriesConfigLoader
from python.framework.types.coverage_report_types import Gap, GapCategory
from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL, SignalParquetColumn)
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

        # Distinct provenance values found (empty when the producer predates the field)
        self.data_origins: Set[str] = set()
        self.config_fingerprints: Set[str] = set()
        # Envelope counts per trigger_reason (scheduled / boot / breaking / manual / external)
        self.trigger_reasons: Dict[str, int] = {}

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

        Reads only the timeline column plus the origin marker (projection) —
        that is all this report needs. Paths are resolved by the caller from the
        signal index, which already scopes files to the symbol.

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
        Read the distinct snapshot timestamps + the origin marker from the parquets.

        Side effect: fills `data_origins` with the distinct data_origin values found.

        Args:
            paths: Signal parquet files to read

        Returns:
            Ascending, de-duplicated snapshot times (UTC-aware)
        """
        msc_column = SignalParquetColumn.COLLECTED_MSC.value
        origin_column = SignalParquetColumn.DATA_ORIGIN.value
        fingerprint_column = SignalParquetColumn.CONFIG_FINGERPRINT.value
        trigger_column = SignalParquetColumn.TRIGGER_REASON.value
        symbol_column = SignalParquetColumn.SYMBOL.value
        wanted = (msc_column, symbol_column, origin_column,
                  fingerprint_column, trigger_column)

        frames = []
        for path in paths:
            # The provenance scalars arrived after the first archives were imported —
            # project each only where the file actually carries it, so a parquet
            # written before the field existed still reads.
            available = set(pq.read_schema(path).names)
            frames.append(pd.read_parquet(
                path, columns=[c for c in wanted if c in available]))

        if not frames:
            return []

        df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

        self.data_origins = self._distinct(df, origin_column)
        self.config_fingerprints = self._distinct(df, fingerprint_column)
        self.trigger_reasons = self._count_per_envelope(
            df, trigger_column, msc_column, symbol_column)

        msc_values = sorted(set(int(v) for v in df[msc_column]))
        return [
            datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc)
            for msc in msc_values
        ]

    def _distinct(self, df: pd.DataFrame, column: str) -> Set[str]:
        """
        Distinct non-empty values of an optional provenance column.

        Args:
            df: Concatenated projection
            column: Column name (may be absent for a pre-contract archive)

        Returns:
            Set of values; empty when the column is absent or never stamped
        """
        if column not in df.columns:
            return set()
        return {str(v) for v in df[column].dropna().unique() if str(v)}

    def _count_per_envelope(self, df: pd.DataFrame, column: str,
                            msc_column: str, symbol_column: str) -> Dict[str, int]:
        """
        Count an envelope-scalar column once per snapshot, not once per row.

        The parquet carries one row per (snapshot, symbol) plus a sentinel, so a
        naive value_counts would weight every snapshot by its symbol count.

        Args:
            df: Concatenated projection
            column: Envelope-scalar column to count (may be absent)
            msc_column: The snapshot key
            symbol_column: The symbol column (used to pick one row per snapshot)

        Returns:
            {value: envelope count}; empty when the column is absent or never stamped
        """
        if column not in df.columns or symbol_column not in df.columns:
            return {}

        # The sentinel row exists for every envelope — exactly one per snapshot.
        sentinels = df[df[symbol_column] == SIGNAL_ENVELOPE_SYMBOL]
        source = sentinels if not sentinels.empty else df.drop_duplicates(msc_column)

        counts = source[column].fillna('').astype(str).value_counts().to_dict()
        return {k: int(v) for k, v in counts.items() if k}

    def _one_or_mixed(self, values: Set[str]) -> str:
        """
        Collapse a provenance value set to a single label.

        Args:
            values: Distinct values found in the archive

        Returns:
            The single value, 'mixed' when several, '' when never stamped
        """
        if not values:
            return ''
        if len(values) > 1:
            return 'mixed'
        return next(iter(values))

    def get_data_origin(self) -> str:
        """
        The archive's origin marker — the mock-versus-real discriminator (#447).

        Returns:
            'synthetic' / 'live' / 'mixed' when a source carries both / '' when the
            producer predates the field (unknown, not an assertion of realness)
        """
        return self._one_or_mixed(self.data_origins)

    def get_scheduled_share(self) -> Optional[float]:
        """
        Share of envelopes produced by the regular bar-close grid.

        The rest are boot, breaking, manual or external passes — real analyses,
        but not points of the scheduled cadence. Replaces the timing heuristic
        ("distance to predecessor < 300s") that misclassifies a slow scheduled
        pass as an off-grid one.

        Returns:
            Ratio 0.0–1.0, or None when the producer predates trigger_reason
        """
        total = sum(self.trigger_reasons.values())
        if not total:
            return None
        return self.trigger_reasons.get('scheduled', 0) / total

    def get_config_fingerprint(self) -> str:
        """
        The producer's input-config hash — the comparability marker.

        Two archive stretches are comparable when prompt_hash AND this agree. A
        'mixed' result means the producer's configuration changed inside the
        archive, so the stretches on either side are NOT one series.

        Returns:
            The fingerprint / 'mixed' when the archive spans several / '' when the
            producer predates the field
        """
        return self._one_or_mixed(self.config_fingerprints)

    def is_synthetic(self) -> bool:
        """
        Whether the archive declares itself generated.

        Returns:
            True when any snapshot carries data_origin 'synthetic'
        """
        return 'synthetic' in self.data_origins

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

        origin = self.get_data_origin()
        if origin == 'synthetic':
            report.append("Origin:       🧪 SYNTHETIC — generated data, not a market record")
        elif origin:
            report.append(f"Origin:       {origin}")
        else:
            report.append("Origin:       unknown (producer predates the data_origin field)")

        fingerprint = self.get_config_fingerprint()
        if fingerprint == 'mixed':
            report.append(
                f"Config:       ⚠️  {len(self.config_fingerprints)} fingerprints — the producer's "
                f"input config changed inside this archive")
        elif fingerprint:
            report.append(f"Config:       #{fingerprint}")
        else:
            report.append("Config:       unknown (producer predates the config_fingerprint field)")

        if self.trigger_reasons:
            parts = ' · '.join(
                f"{count:,} {reason}"
                for reason, count in sorted(
                    self.trigger_reasons.items(), key=lambda kv: -kv[1]))
            report.append(f"Triggers:     {parts}")
        else:
            report.append("Triggers:     unknown (producer predates the trigger_reason field)")

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
