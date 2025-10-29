"""
TimeRangeCoverageReport - Data Continuity Analysis
Validates time range coverage and detects gaps in tick data

NEW  Gap detection and human-readable coverage reports
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict
from pathlib import Path

from python.framework.utils.market_calendar import MarketCalendar, GapCategory


@dataclass
class IndexEntry:
    """
    Represents a single Parquet file in the index.
    """
    file: str
    path: str
    symbol: str
    start_time: datetime
    end_time: datetime
    tick_count: int
    file_size_mb: float
    source_file: str
    num_row_groups: int


@dataclass
class Gap:
    """
    Represents a time gap between two files.
    """
    file1: IndexEntry
    file2: IndexEntry
    gap_seconds: float
    category: GapCategory
    reason: str

    @property
    def gap_hours(self) -> float:
        """Gap duration in hours"""
        return self.gap_seconds / 3600

    @property
    def duration_human(self) -> str:
        """Human-readable duration"""
        return MarketCalendar.format_duration(self.gap_seconds)

    @property
    def severity_icon(self) -> str:
        """Icon based on severity"""
        return {
            GapCategory.SEAMLESS: '✅',
            GapCategory.WEEKEND: '✅',
            GapCategory.SHORT: '⚠️ ',
            GapCategory.MODERATE: '⚠️ ',
            GapCategory.LARGE: '🔴'
        }.get(self.category, '❓')


class TimeRangeCoverageReport:
    """
    Analyzes time range coverage and generates reports.

    Features:
    - Gap detection between files
    - Weekend vs. data loss classification
    - Human-readable reports
    - Actionable recommendations
    """

    def __init__(self, symbol: str, files: List[IndexEntry]):
        """
        Initialize coverage report.

        Args:
            symbol: Trading symbol
            files: List of index entries (must be sorted chronologically)
        """
        self.symbol = symbol
        self.files = sorted(files, key=lambda x: x.start_time)

        # Analysis results
        self.gaps: List[Gap] = []
        self.gap_counts = {
            'seamless': 0,
            'weekend': 0,
            'short': 0,
            'moderate': 0,
            'large': 0
        }

        # Metadata
        self.total_ticks = sum(f.tick_count for f in files)
        self.total_size_mb = sum(f.file_size_mb for f in files)
        self.start_time = files[0].start_time if files else None
        self.end_time = files[-1].end_time if files else None

    def analyze(self) -> None:
        """
        Analyze all files for continuity and gaps.
        """
        if len(self.files) < 2:
            # Single file or no files - no gaps to analyze
            return

        # Check each transition between files
        for i in range(len(self.files) - 1):
            current = self.files[i]
            next_file = self.files[i + 1]

            # Calculate gap
            gap_seconds = (next_file.start_time -
                           current.end_time).total_seconds()

            # Classify gap
            category, reason = MarketCalendar.classify_gap(
                current.end_time,
                next_file.start_time,
                gap_seconds
            )

            # Create gap object
            gap = Gap(
                file1=current,
                file2=next_file,
                gap_seconds=gap_seconds,
                category=category,
                reason=reason
            )

            self.gaps.append(gap)
            self.gap_counts[category.value] += 1

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
        report.append(f"📊 DATA COVERAGE REPORT: {self.symbol}")
        report.append(f"{'='*60}")
        report.append(f"Files:        {len(self.files)}")

        if self.start_time and self.end_time:
            report.append(
                f"Time Range:   {self.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            report.append(
                f"           → {self.end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")

            duration = self.end_time - self.start_time
            duration_days = duration.days
            duration_hours = duration.total_seconds() / 3600
            report.append(
                f"Duration:     {duration_days}d {int(duration_hours % 24)}h")

        report.append(f"Total Ticks:  {self.total_ticks:,}")
        report.append(f"Total Size:   {self.total_size_mb:.1f} MB")

        # === SECTION 2: Gap Summary ===
        report.append(f"\n{'─'*60}")
        report.append("GAP ANALYSIS:")
        report.append(f"{'─'*60}")
        report.append(
            f"✅ Seamless:     {self.gap_counts['seamless']} transitions")
        report.append(
            f"✅ Weekend:      {self.gap_counts['weekend']} gaps (expected)")
        report.append(
            f"⚠️  Short:        {self.gap_counts['short']} gaps (< 30 min)")
        report.append(
            f"⚠️  Moderate:     {self.gap_counts['moderate']} gaps (30min - 4h)")
        report.append(
            f"🔴 Large:        {self.gap_counts['large']} gaps (> 4h)")

        # === SECTION 3: Detailed Gap List ===
        problematic_gaps = [g for g in self.gaps if g.category in [
            GapCategory.MODERATE, GapCategory.LARGE]]

        if problematic_gaps:
            report.append(f"\n{'─'*60}")
            report.append("⚠️  GAP DETAILS:")
            report.append(f"{'─'*60}")

            for gap in problematic_gaps:
                report.append(
                    f"\n{gap.severity_icon} {gap.category.value.upper()} GAP:")
                report.append(f"   File 1: {gap.file1.file}")
                report.append(
                    f"   End:    {gap.file1.end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                report.append(f"   File 2: {gap.file2.file}")
                report.append(
                    f"   Start:  {gap.file2.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
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
                report.append(
                    f"   {gap.file1.end_time.strftime('%Y-%m-%d %H:%M')} → "
                    f"{gap.file2.start_time.strftime('%H:%M')} ({gap.duration_human})"
                )

        # === SECTION 4: Recommendations ===
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

    def get_summary_dict(self) -> Dict:
        """
        Get report summary as dictionary (for programmatic access).

        Returns:
            Dict with summary statistics
        """
        return {
            'symbol': self.symbol,
            'num_files': len(self.files),
            'total_ticks': self.total_ticks,
            'total_size_mb': round(self.total_size_mb, 2),
            'time_range': {
                'start': self.start_time.isoformat() if self.start_time else None,
                'end': self.end_time.isoformat() if self.end_time else None,
                'duration_days': (self.end_time - self.start_time).days if self.start_time and self.end_time else 0
            },
            'gap_counts': self.gap_counts,
            'has_issues': self.has_issues(),
            'problematic_gaps': [
                {
                    'file1': g.file1.file,
                    'file2': g.file2.file,
                    'gap_hours': round(g.gap_hours, 2),
                    'category': g.category.value,
                    'reason': g.reason
                }
                for g in self.gaps
                if g.category in [GapCategory.MODERATE, GapCategory.LARGE]
            ]
        }
