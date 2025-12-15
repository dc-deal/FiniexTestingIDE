"""
Market Calendar - Forex Market Hours and Weekend Detection
Handles market open/close times and weekend gaps

EXTENDED  Comprehensive weekend analysis and gap classification
ENHANCED  Calendar-based weekend detection for extended gaps
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Union

import pytz

from python.framework.types.coverage_report_types import GapCategory
from python.framework.types.market_types import WeekendClosureWindow


class MarketCalendar:
    """
    Forex market calendar for weekend-aware operations.

    Forex market hours: Monday 00:00 UTC - Friday 23:59 UTC
    Closed: Saturday and Sunday

    EXTENDED  Added detailed weekend statistics and gap classification
    ENHANCED  Calendar-based weekend detection for data outages spanning weekends
    """

    # Weekend closure window configuration
    WEEKEND_CLOSURE = WeekendClosureWindow()

    @staticmethod
    def get_weekend_closure_description() -> str:
        """
        Get human-readable description of expected weekend closure window.

        Returns:
            Formatted multi-line string describing market closure timing
        """
        return MarketCalendar.WEEKEND_CLOSURE.get_description()

    @staticmethod
    def is_market_open(timestamp: datetime) -> bool:
        """
        Check if market is open at given timestamp.

        Args:
            timestamp: Datetime to check

        Returns:
            True if market is open (Monday-Friday), False otherwise
        """
        weekday = timestamp.weekday()
        return 0 <= weekday <= 4  # Monday=0, Friday=4

    @staticmethod
    def spans_weekend(start: datetime, end: datetime) -> Tuple[bool, int]:
        """
        Check if time period spans a weekend and count weekend days.

        Args:
            start: Start timestamp
            end: End timestamp

        Returns:
            Tuple of (spans_weekend: bool, weekend_days: int)
        """
        if start > end:
            raise ValueError("Start time must be before end time")

        days_between = (end - start).days

        # Quick check: If less than a day apart, no weekend
        if days_between == 0:
            # Unless it crosses into weekend
            if start.weekday() <= 4 and end.weekday() >= 5:
                return True, 1
            return False, 0

        # Count weekend days in range
        weekend_days = 0
        current = start

        while current <= end:
            if current.weekday() >= 5:  # Saturday=5, Sunday=6
                weekend_days += 1
            current += timedelta(days=1)

        return weekend_days > 0, weekend_days

    @staticmethod
    def validate_timeframe_for_weekends(timeframe: str) -> None:
        """
        Validate that timeframe is supported for weekend-aware operations.

        Daily and higher timeframes require special weekend handling.

        Args:
            timeframe: Timeframe string (e.g., 'M5', 'H1', 'D1')

        Raises:
            NotImplementedError: If timeframe requires weekend logic not yet implemented
        """
        daily_and_higher = ['D1', 'W1', 'MN']

        if timeframe in daily_and_higher:
            raise NotImplementedError(
                f"❌ Timeframe '{timeframe}' not yet supported!\n"
                f"   Reason: Requires advanced weekend gap handling\n"
                f"   Daily and higher timeframes span multiple weeks\n"
                f"   → Use intraday timeframes (M1-H4) for now"
            )

    @staticmethod
    def get_trading_days(start: datetime, end: datetime) -> int:
        """
        Count trading days (Mon-Fri) between two timestamps.

        Args:
            start: Start timestamp
            end: End timestamp

        Returns:
            Number of trading days in range
        """
        if start > end:
            raise ValueError("Start time must be before end time")

        trading_days = 0
        current = start.date()
        end_date = end.date()

        while current <= end_date:
            if current.weekday() <= 4:  # Monday-Friday
                trading_days += 1
            current += timedelta(days=1)

        return trading_days

    @staticmethod
    def get_previous_trading_day(timestamp: datetime) -> datetime:
        """
        Get the previous trading day from given timestamp.
        Useful for finding Friday when starting on weekend.

        Args:
            timestamp: Reference timestamp

        Returns:
            Datetime of previous trading day
        """
        current = timestamp - timedelta(days=1)

        # Go back until we hit a weekday
        while current.weekday() >= 5:  # Skip weekends
            current -= timedelta(days=1)

        return current

    # =========================================================================
    # EXTENDED WEEKEND ANALYSIS
    # =========================================================================

    @staticmethod
    def get_weekend_statistics(start: datetime, end: datetime) -> Dict:
        """
        Get detailed weekend statistics for a time range.

         from TickDataAnalyzer._count_weekends() for central location.

        Args:
            start: Start timestamp
            end: End timestamp

        Returns:
            Dict with detailed weekend statistics:
            {
                'full_weekends': int,
                'saturdays': int,
                'sundays': int,
                'total_weekend_days': int,
                'start_is_weekend': bool,
                'end_is_weekend': bool,
                'weekend_percentage': float
            }
        """
        if start > end:
            raise ValueError("Start time must be before end time")

        # Number of full weeks
        full_weeks = (end - start).days // 7

        # Count Saturdays and Sundays separately
        saturdays = full_weeks
        sundays = full_weeks

        # Check remaining days
        remaining_days = (end - start).days % 7
        current_date = start

        for _ in range(remaining_days + 1):
            if current_date.weekday() == 5:  # Saturday
                saturdays += 1
            elif current_date.weekday() == 6:  # Sunday
                sundays += 1
            current_date += timedelta(days=1)

        # Weekend days (Sat+Sun together)
        weekend_days = saturdays + sundays

        # Number of complete weekends (Sat+Sun pairs)
        full_weekends = min(saturdays, sundays)

        # Calculate weekend percentage
        total_days = (end - start).days + 1
        weekend_percentage = (weekend_days / total_days *
                              100) if total_days > 0 else 0

        # Check if boundaries fall on weekend
        start_weekday = start.weekday()
        end_weekday = end.weekday()

        return {
            "full_weekends": full_weekends,
            "saturdays": saturdays,
            "sundays": sundays,
            "total_weekend_days": weekend_days,
            "start_is_weekend": start_weekday >= 5,
            "end_is_weekend": end_weekday >= 5,
            "weekend_percentage": round(weekend_percentage, 2)
        }

    @staticmethod
    def gap_contains_weekend(start: datetime, end: datetime) -> bool:
        """
        Check if time gap contains any weekend days (Saturday or Sunday).

        Uses calendar-based detection independent of gap pattern.
        Useful for detecting weekends in data outages that don't follow
        typical Friday evening → Monday morning pattern.

        Args:
            start: Gap start timestamp
            end: Gap end timestamp

        Returns:
            True if gap contains at least one Saturday or Sunday
        """
        current = start.date()
        end_date = end.date()

        while current <= end_date:
            if current.weekday() in [5, 6]:  # Saturday=5, Sunday=6
                return True
            current += timedelta(days=1)

        return False

    @staticmethod
    def classify_gap(
        start: datetime,
        end: datetime,
        gap_seconds: float,
        thresholds: Optional[Dict[str, float]] = None
    ) -> Tuple[GapCategory, str]:
        """
        Classify a time gap between two timestamps.

        Used for data continuity validation and gap analysis.

        Args:
            start: Gap start timestamp (end of first file)
            end: Gap end timestamp (start of second file)
            gap_seconds: Gap duration in seconds

        Returns:
            Tuple of (GapCategory, reason_string)

        Categories:
            - SEAMLESS: < 5 seconds (perfect continuity)
            - WEEKEND: Fr evening → Mo morning, 40-80 hours OR any gap ≥24h containing Sat/Sun
            - SHORT: 5s - 30min (connection blip, restart)
            - MODERATE: 30min - 4h (potential data loss)
            - LARGE: > 4h (significant data loss)
        """
        # Default thresholds if not provided
        if thresholds is None:
            thresholds = {
                'short': 0.5,      # < 30 minutes
                'moderate': 4.0    # 30min - 4h
            }

        gap_hours = gap_seconds / 3600

        # 1. SEAMLESS (< 5 seconds)
        if gap_seconds < 5:
            return GapCategory.SEAMLESS, '✅ Perfect continuity'

        # 2. WEEKEND CHECK
        # Forex market typically closes Friday ~21:00 UTC, opens Monday ~00:00 UTC
        # Allow flexible range 40-80 hours to account for timezone variations
        start_day = start.weekday()
        end_day = end.weekday()

        # Primary pattern: Friday evening → Monday morning
        if MarketCalendar.WEEKEND_CLOSURE.matches_primary_pattern(
            start_day, start.hour, end_day, end.hour, gap_hours
        ):
            return GapCategory.WEEKEND, f'✅ Normal weekend gap ({gap_hours:.1f}h)'

        # Alternative weekend pattern: Saturday → Monday
        if MarketCalendar.WEEKEND_CLOSURE.matches_alternative_pattern(
            start_day, end_day, end.hour, gap_hours
        ):
            return GapCategory.WEEKEND, f'✅ Weekend gap (Sat→Mon, {gap_hours:.1f}h)'

        # Extended weekend detection: Calendar-based fallback
        # Handles data outages that span weekends but don't match typical patterns
        # Example: Wed 15:40 → Mon 01:57 (contains Sat+Sun but doesn't start Friday)
        if MarketCalendar.gap_contains_weekend(start, end) and gap_hours >= 24:
            return GapCategory.WEEKEND, f'✅ Weekend gap (extended, {gap_hours:.1f}h)'

        # 3. SHORT GAP
        # Common causes: Server restart, connection blip, MT5 restart
        if gap_hours < thresholds['short']:
            return GapCategory.SHORT, f'⚠️  Short interruption ({int(gap_seconds/60)} min - restart/connection?)'

        # 4. MODERATE GAP
        # Potential data loss, but could be planned maintenance
        if gap_hours < thresholds['moderate']:
            if MarketCalendar.is_market_open(start):
                return GapCategory.MODERATE, f'⚠️  Moderate gap during trading hours ({gap_hours:.2f}h)'
            else:
                return GapCategory.MODERATE, f'ℹ️  Moderate gap outside trading hours ({gap_hours:.2f}h)'

        # 5. LARGE GAP (> 4h)
        # Significant data loss - should be investigated
        return GapCategory.LARGE, f'🔴 Large gap - check data collection ({gap_hours:.2f}h)'
