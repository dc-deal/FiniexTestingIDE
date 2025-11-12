"""
Time utility functions for readable duration formatting
"""


def format_duration(seconds: float, show_milliseconds: bool = False) -> str:
    """
    Format duration in human-readable format.

    Args:
        seconds: Duration in seconds
        show_milliseconds: If True, show milliseconds for durations < 1 minute

    Returns:
        Formatted duration (e.g., "30m 28s", "2h 15m 32s", "12s 200ms")
    """
    if seconds < 60:
        # Less than 1 minute: show seconds (and optionally milliseconds)
        whole_seconds = int(seconds)

        if show_milliseconds:
            milliseconds = int((seconds - whole_seconds) * 1000)
            if milliseconds > 0:
                return f"{whole_seconds}s {milliseconds}ms"
            else:
                return f"{whole_seconds}s"
        else:
            return f"{whole_seconds}s"

    minutes = int(seconds // 60)
    remaining_seconds = int(seconds % 60)

    if minutes < 60:
        # Less than 1 hour: show minutes and seconds (no milliseconds)
        if remaining_seconds > 0:
            return f"{minutes}m {remaining_seconds}s"
        else:
            return f"{minutes}m"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    # 1 hour or more: show hours and minutes (skip seconds for brevity)
    if remaining_minutes > 0:
        return f"{hours}h {remaining_minutes}m"
    else:
        return f"{hours}h"


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
