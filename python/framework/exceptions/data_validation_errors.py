"""
FiniexTestingIDE - Data Validation Exceptions
Custom exceptions for data preparation and validation

Location: python/framework/exceptions/data_validation_errors.py
"""

from datetime import datetime
from typing import Dict, Any, Optional


class DataValidationError(Exception):
    """Base class for all data validation errors"""

    def get_context(self) -> Dict[str, Any]:
        """Get error context for logger"""
        return {}


class InsufficientTickDataError(DataValidationError):
    """
    Raised when not enough ticks available for tick-limited mode.

    Used in: Modus A (max_ticks mode)
    """

    def __init__(
        self,
        scenario_name: str,
        required_ticks: int,
        available_ticks: int,
        start_date: datetime,
        symbol: str
    ):
        self.scenario_name = scenario_name
        self.required_ticks = required_ticks
        self.available_ticks = available_ticks
        self.start_date = start_date
        self.symbol = symbol

        message = (
            f"Insufficient tick data for scenario '{scenario_name}'!\n"
            f"   Required: {required_ticks:,} ticks from {start_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"   Available: {available_ticks:,} ticks\n"
            f"   Symbol: {symbol}\n\n"
            f"💡 Suggestions:\n"
            f"   • Move start_date earlier to get more ticks\n"
            f"   • Reduce max_ticks to {available_ticks:,}\n"
            f"   • Check if data collection is complete"
            f"   • Check Data Coverage/Gap Report"
        )

        super().__init__(message)

    def get_context(self) -> Dict[str, Any]:
        return {
            'scenario': self.scenario_name,
            'required_ticks': self.required_ticks,
            'available_ticks': self.available_ticks,
            'start_date': self.start_date.strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': self.symbol,
            'shortfall': self.required_ticks - self.available_ticks
        }


class CriticalGapError(DataValidationError):
    """
    Raised when critical gaps detected in test timespan.

    Used in: Modus B (timespan mode)
    """

    def __init__(
        self,
        scenario_name: str,
        symbol: str,
        test_start: datetime,
        test_end: datetime,
        gaps: list,
        smallest_timeframe: str
    ):
        self.scenario_name = scenario_name
        self.symbol = symbol
        self.test_start = test_start
        self.test_end = test_end
        self.gaps = gaps
        self.smallest_timeframe = smallest_timeframe

        # Build gap details
        gap_details = "\n".join([
            f"   • {gap.duration_human} gap: "
            f"{gap.file1.end_time.strftime('%Y-%m-%d %H:%M')} → "
            f"{gap.file2.start_time.strftime('%H:%M')}"
            for gap in gaps[:5]  # Show first 5
        ])

        message = (
            f"Critical gaps detected in test period!\n"
            f"   Scenario: {scenario_name}\n"
            f"   Symbol: {symbol}\n"
            f"   Period: {test_start.strftime('%Y-%m-%d %H:%M')} → {test_end.strftime('%Y-%m-%d %H:%M')}\n"
            f"   Smallest timeframe: {smallest_timeframe}\n\n"
            f"   Gaps found:\n"
            f"{gap_details}\n\n"
            f"💡 These gaps will corrupt bar rendering!\n"
            f"   → Re-collect data for this period or choose different timespan"
        )

        super().__init__(message)

    def get_context(self) -> Dict[str, Any]:
        return {
            'scenario': self.scenario_name,
            'symbol': self.symbol,
            'test_start': self.test_start.strftime('%Y-%m-%d %H:%M:%S'),
            'test_end': self.test_end.strftime('%Y-%m-%d %H:%M:%S'),
            'smallest_timeframe': self.smallest_timeframe,
            'gap_count': len(self.gaps),
            'largest_gap': self.gaps[0].duration_human if self.gaps else None
        }


class NoDataAvailableError(DataValidationError):
    """
    Raised when no data found for symbol/period.

    Used in: Initial data load
    """

    def __init__(
        self,
        symbol: str,
        start_date: datetime,
        load_end: datetime
    ):
        self.symbol = symbol
        self.start_date = start_date
        self.load_end = load_end

        message = (
            f"No tick data found for symbol!\n"
            f"   Symbol: {symbol}\n"
            f"   Period: {start_date.strftime('%Y-%m-%d %H:%M')} → {load_end.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"💡 Possible causes:\n"
            f"   • Symbol not in data collection\n"
            f"   • Start date is after available data\n"
            f"   • Data collection incomplete"
        )

        super().__init__(message)

    def get_context(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'start_date': self.start_date.strftime('%Y-%m-%d %H:%M:%S'),
            'load_end': self.load_end.strftime('%Y-%m-%d %H:%M:%S')
        }


class NoTicksInTimespanError(DataValidationError):
    """
    Raised when no ticks found in specified timespan.

    Used in: Modus B (timespan mode) when test_df is empty
    """

    def __init__(
        self,
        scenario_name: str,
        symbol: str,
        test_start: datetime,
        test_end: datetime
    ):
        self.scenario_name = scenario_name
        self.symbol = symbol
        self.test_start = test_start
        self.test_end = test_end

        message = (
            f"No ticks found in test period!\n"
            f"   Scenario: {scenario_name}\n"
            f"   Symbol: {symbol}\n"
            f"   Period: {test_start.strftime('%Y-%m-%d %H:%M')} → {test_end.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"💡 Check:\n"
            f"   • Is this period covered by data collection?\n"
            f"   • Did you mean a different date range?"
        )

        super().__init__(message)

    def get_context(self) -> Dict[str, Any]:
        return {
            'scenario': self.scenario_name,
            'symbol': self.symbol,
            'test_start': self.test_start.strftime('%Y-%m-%d %H:%M:%S'),
            'test_end': self.test_end.strftime('%Y-%m-%d %H:%M:%S')
        }


class InvalidDateRangeError(DataValidationError):
    """
    Raised when scenario has invalid date range.

    Used in: config_loader validation
    """

    def __init__(
        self,
        scenario_name: str,
        start_date: str,
        end_date: str,
        max_ticks: Optional[int] = None
    ):
        self.scenario_name = scenario_name
        self.start_date = start_date
        self.end_date = end_date
        self.max_ticks = max_ticks

        if max_ticks:
            mode_info = "tick-limited mode"
            suggestion = "Set end_date = start_date or later"
        else:
            mode_info = "timespan mode"
            suggestion = "end_date must be after start_date"

        message = (
            f"Invalid date range in scenario configuration!\n"
            f"   Scenario: {scenario_name}\n"
            f"   Start: {start_date}\n"
            f"   End: {end_date}\n"
            f"   Mode: {mode_info}\n\n"
            f"💡 Fix: {suggestion}"
        )

        super().__init__(message)

    def get_context(self) -> Dict[str, Any]:
        return {
            'scenario': self.scenario_name,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'max_ticks': self.max_ticks,
            'mode': 'tick-limited' if self.max_ticks else 'timespan'
        }
