"""
Market-related type definitions

Contains dataclasses and types for market calendar operations,
weekend closure windows, and trading hour configurations.
"""

from dataclasses import dataclass
from typing import Tuple

from python.framework.types.market_config_types import MarketType


# Validation timezone for UTC offset verification
VALIDATION_TIMEZONE = 'Europe/Berlin'


@dataclass
class WeekendClosureWindow:
    """
    Configuration for weekend market closure detection.

    Defines expected timing and duration ranges for normal weekend gaps
    in forex market data (Friday evening → Monday morning).

    Attributes:
        friday_start_hour_utc: Hour (UTC) when Friday closure begins (>= this hour)
        sunday_end_hour_utc : Hour (UTC) when Monday open ends (<= this hour)
        min_duration_hours: Minimum expected weekend gap duration
        max_duration_hours: Maximum expected weekend gap duration
        alt_min_duration_hours: Alternative pattern minimum (Saturday → Monday)
        alt_max_duration_hours: Alternative pattern maximum (Saturday → Monday)
    """
    friday_start_hour_utc: int = 20
    sunday_end_hour_utc: int = 22
    min_duration_hours: int = 40
    max_duration_hours: int = 80
    alt_min_duration_hours: int = 24
    alt_max_duration_hours: int = 50

    def get_description(self) -> str:
        """
        Generate human-readable description of closure window.

        Returns:
            Formatted multi-line string for reports
        """
        lines = [
            f"• Friday from {self.friday_start_hour_utc:02d}:00 UTC → Monday until {self.sunday_end_hour_utc:02d}:00 UTC",
            f"• Expected duration: {self.min_duration_hours}-{self.max_duration_hours} hours",
            f"• Alternative: Saturday → Monday ({self.alt_min_duration_hours}-{self.alt_max_duration_hours}h)"
        ]
        return "\n".join(lines)

    def matches_primary_pattern(
        self,
        start_weekday: int,
        start_hour: int,
        end_weekday: int,
        end_hour: int,
        gap_hours: float
    ) -> bool:
        """
        Check if gap matches primary Friday → Monday pattern.

        Args:
            start_weekday: Start day (0=Monday, 4=Friday)
            start_hour: Start hour UTC
            end_weekday: End day (0=Monday)
            end_hour: End hour UTC
            gap_hours: Gap duration in hours

        Returns:
            True if matches primary weekend pattern
        """
        is_friday_evening = (
            start_weekday == 4 and start_hour >= self.friday_start_hour_utc)
        is_monday_morning = (end_weekday == 6 and end_hour <=
                             self.sunday_end_hour_utc)
        duration_match = self.min_duration_hours <= gap_hours <= self.max_duration_hours

        return is_friday_evening and is_monday_morning and duration_match

    def matches_alternative_pattern(
        self,
        start_weekday: int,
        end_weekday: int,
        end_hour: int,
        gap_hours: float
    ) -> bool:
        """
        Check if gap matches alternative Saturday → Monday pattern.

        Args:
            start_weekday: Start day (5=Saturday)
            end_weekday: End day (0=Monday)
            end_hour: End hour UTC
            gap_hours: Gap duration in hours

        Returns:
            True if matches alternative weekend pattern
        """
        is_saturday = (start_weekday == 5)
        is_monday_morning = (end_weekday == 6 and end_hour <=
                             self.sunday_end_hour_utc)
        duration_match = self.alt_min_duration_hours <= gap_hours <= self.alt_max_duration_hours

        return is_saturday and is_monday_morning and duration_match


@dataclass
class TradingContext:
    """
    Static metadata about the trading environment.

    Passed to Workers and Decision Logic at creation time.
    Contains all broker/market information they need to know.

    Philosophy: "Tell, don't ask" - give them the context upfront,
    instead of letting them query services.

    Attributes:
        broker_type: Broker identifier (e.g., 'mt5', 'kraken_spot')
        market_type: Market classification (FOREX, CRYPTO, STOCKS, etc.)
        symbol: Trading symbol for this scenario
    """
    broker_type: str  # BrokerType as string for serialization
    market_type: MarketType
    symbol: str
