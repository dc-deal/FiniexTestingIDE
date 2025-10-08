"""
Time utility functions for readable duration formatting
"""

from datetime import datetime


def format_duration(start: datetime, end: datetime) -> str:
    """
    Format time duration in human-readable format.

    Args:
        start: Start datetime
        end: End datetime

    Returns:
        Formatted string like "1h 45m", "45m", or "2d 3h"
    """
    duration = end - start
    total_seconds = int(duration.total_seconds())

    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")

    return " ".join(parts) if parts else "0m"


def format_seconds(seconds: float) -> str:
    """
    Format seconds in readable format.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "12.5s" or "2m 30s"
    """
    if seconds < 60:
        return f"{seconds:.2f}s"

    minutes = int(seconds // 60)
    remaining_seconds = int(seconds % 60)

    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    return f"{hours}h {remaining_minutes}m {remaining_seconds}s"


def format_minutes(minutes: int) -> str:
    """Format minutes into readable duration like '1h 45m' or '2d 3h'"""
    if minutes < 60:
        return f"{minutes}m"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    if hours < 24:
        return f"{hours}h {remaining_minutes}m" if remaining_minutes else f"{hours}h"

    days = hours // 24
    remaining_hours = hours % 24

    parts = [f"{days}d"]
    if remaining_hours:
        parts.append(f"{remaining_hours}h")
    if remaining_minutes:
        parts.append(f"{remaining_minutes}m")

    return " ".join(parts)
