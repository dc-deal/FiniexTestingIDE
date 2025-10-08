"""
Market Calendar - Forex Market Hours and Weekend Detection
Handles market open/close times and weekend gaps
"""

from datetime import datetime, timedelta
from typing import Tuple


class MarketCalendar:
    """
    Simple Forex market calendar for weekend-aware operations.

    Forex market hours: Monday 00:00 UTC - Friday 23:59 UTC
    Closed: Saturday and Sunday
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
