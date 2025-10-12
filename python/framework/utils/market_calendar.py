"""
Market Calendar - Forex Market Hours and Weekend Detection
Handles market open/close times and weekend gaps

EXTENDED (C#002): Comprehensive weekend analysis and gap classification
"""

from datetime import datetime, timedelta
from typing import Tuple, Dict, Union
from enum import Enum

import pytz


class GapCategory(Enum):
    """Gap classification categories"""
    SEAMLESS = "seamless"
    WEEKEND = "weekend"
    SHORT = "short"
    MODERATE = "moderate"
    LARGE = "large"


class MarketCalendar:
    """
    Forex market calendar for weekend-aware operations.

    Forex market hours: Monday 00:00 UTC - Friday 23:59 UTC
    Closed: Saturday and Sunday

    EXTENDED (C#002): Added detailed weekend statistics and gap classification
    """

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
    # NEW (C#002): EXTENDED WEEKEND ANALYSIS
    # =========================================================================

    @staticmethod
    def get_weekend_statistics(start: datetime, end: datetime) -> Dict:
        """
        Get detailed weekend statistics for a time range.

        Refactored from TickDataAnalyzer._count_weekends() for central location.

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
    def classify_gap(
        start: datetime,
        end: datetime,
        gap_seconds: float
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
            - WEEKEND: Fr evening → Mo morning, 40-80 hours
            - SHORT: 5s - 30min (connection blip, restart)
            - MODERATE: 30min - 4h (potential data loss)
            - LARGE: > 4h (significant data loss)
        """
        gap_hours = gap_seconds / 3600

        # 1. SEAMLESS (< 5 seconds)
        if gap_seconds < 5:
            return GapCategory.SEAMLESS, '✅ Perfect continuity'

        # 2. WEEKEND CHECK
        # Forex market typically closes Friday ~21:00 UTC, opens Monday ~00:00 UTC
        # Allow flexible range 40-80 hours to account for timezone variations
        start_day = start.weekday()
        end_day = end.weekday()

        is_friday_evening = (
            start_day == 4 and start.hour >= 20)  # Fr after 20:00
        is_monday_morning = (end_day == 0 and end.hour <=
                             2)       # Mo before 02:00

        if is_friday_evening and is_monday_morning and 40 <= gap_hours <= 80:
            return GapCategory.WEEKEND, f'✅ Normal weekend gap ({gap_hours:.1f}h)'

        # Alternative weekend pattern: Saturday → Monday
        is_saturday = (start_day == 5)
        if is_saturday and is_monday_morning and 24 <= gap_hours <= 50:
            return GapCategory.WEEKEND, f'✅ Weekend gap (Sat→Mon, {gap_hours:.1f}h)'

        # 3. SHORT GAP (< 30 min)
        # Common causes: Server restart, connection blip, MT5 restart
        if gap_hours < 0.5:  # < 30 minutes
            return GapCategory.SHORT, f'⚠️  Short interruption ({int(gap_seconds/60)} min - restart/connection?)'

        # 4. MODERATE GAP (30 min - 4h)
        # Potential data loss, but could be planned maintenance
        if gap_hours < 4:
            if MarketCalendar.is_market_open(start):
                return GapCategory.MODERATE, f'⚠️  Moderate gap during trading hours ({gap_hours:.2f}h)'
            else:
                return GapCategory.MODERATE, f'ℹ️  Moderate gap outside trading hours ({gap_hours:.2f}h)'

        # 5. LARGE GAP (> 4h)
        # Significant data loss - should be investigated
        return GapCategory.LARGE, f'🔴 Large gap - check data collection ({gap_hours:.2f}h)'

    @staticmethod
    def format_duration(seconds: float) -> str:
        """
        Format duration in human-readable format.

        Helper for gap reporting.

        Args:
            seconds: Duration in seconds

        Returns:
            Formatted string (e.g., "2h 15m", "45s", "3d 5h")
        """
        if seconds < 60:
            return f"{int(seconds)}s"

        minutes = seconds / 60
        if minutes < 60:
            return f"{int(minutes)}m"

        hours = minutes / 60
        if hours < 24:
            h = int(hours)
            m = int((hours - h) * 60)
            return f"{h}h {m}m" if m > 0 else f"{h}h"

        days = hours / 24
        d = int(days)
        h = int((days - d) * 24)
        return f"{d}d {h}h" if h > 0 else f"{d}d"

    @staticmethod
    def format_time_range(
        start: Union[datetime, str],
        end: Union[datetime, str],
        date_format: str = "%d.%m.%Y",
        time_format: str = "%H:%M:%S",
        timezone: str = "UTC"
    ) -> str:
        """
        Format time range in human-readable format.
        Shows date only once if both timestamps are on the same day.

        Args:
            start: Start datetime
            end: End datetime
            date_format: Date format (default: DD.MM.YYYY)
            time_format: Time format (default: HH:MM:SS)
            timezone: Timezone for display (default: UTC)

        Returns:
            Formatted string like "23.09.2025 10:30:00 → 13:00:00"
            or "23.09.2025 23:30:00 → 24.09.2025 01:00:00" for different days

        Examples:
            >>> start = datetime(2025, 9, 23, 10, 30, 0, tzinfo=pytz.UTC)
            >>> end = datetime(2025, 9, 23, 13, 0, 0, tzinfo=pytz.UTC)
            >>> format_time_range(start, end)
            '23.09.2025 10:30:00 → 13:00:00'
        """
        # Convert strings to datetime if needed
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace('Z', '+00:00'))
        if isinstance(end, str):
            end = datetime.fromisoformat(end.replace('Z', '+00:00'))

        # Ensure timezone awareness
        if start.tzinfo is None:
            start = pytz.UTC.localize(start)
        if end.tzinfo is None:
            end = pytz.UTC.localize(end)

        # Convert to desired timezone if specified
        if timezone != "UTC":
            tz = pytz.timezone(timezone)
            start = start.astimezone(tz)
            end = end.astimezone(tz)

        # Check if same day
        same_day = start.date() == end.date()

        if same_day:
            # Date only once: "23.09.2025 10:30:00 → 13:00:00"
            return f"{start.strftime(date_format)} {start.strftime(time_format)} → {end.strftime(time_format)}"
        else:
            # Different days: "23.09.2025 23:30:00 → 24.09.2025 01:00:00"
            return (f"{start.strftime(date_format)} {start.strftime(time_format)} → "
                    f"{end.strftime(date_format)} {end.strftime(time_format)}")

    # Vereinfachte Variante ohne Sekunden
    @staticmethod
    def format_time_range_short(start: Union[datetime, str], end: Union[datetime, str]) -> str:
        """Shortened version without seconds: '23.09.2025 10:30 → 13:00'"""
        return MarketCalendar.format_time_range(start, end, time_format="%H:%M")
