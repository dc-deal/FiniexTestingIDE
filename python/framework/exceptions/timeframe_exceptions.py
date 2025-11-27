"""
Timeframe Exceptions
Centralized exceptions for timeframe validation and configuration errors.
"""

from typing import Optional


class TimeframeError(ValueError):
    """
    Base exception for timeframe-related errors.
    """


class UnsupportedTimeframeError(TimeframeError):
    """
    Exception for unsupported timeframe inputs.

    Args:
        timeframe: Timeframe string that was requested.
        message: Optional additional message.
    """

    def __init__(self, timeframe: str, message: Optional[str] = None):
        msg = message or f"Unsupported timeframe: {timeframe}"
        super().__init__(msg)
        self.timeframe = timeframe


class TimeframeConfigError(TimeframeError):
    """
    General configuration error for timeframe registry.

    Args:
        message: Description of the issue.
    """

    def __init__(self, message: str):
        super().__init__(message)
