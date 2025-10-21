"""
FiniexTestingIDE - Thread Utilities
Helper functions for thread management
"""

import re


def sanitize_thread_name(name: str, max_length: int = 20) -> str:
    """
    Sanitize name for thread naming.

    Converts names to thread-safe format:
    - Converts to snake_case
    - Removes special characters
    - Truncates to max_length
    - Removes trailing underscores

    Examples:
        "CORE/rsi" → "core_rsi"
        "eurusd_2024-06-01_window1" → "eurusd_2024_06_01_w"
        "My Strategy!" → "my_strategy"

    Args:
        name: Original name to sanitize
        max_length: Maximum length (default: 20)

    Returns:
        Sanitized thread-safe name
    """
    # Convert to lowercase
    name = name.lower()

    # Replace separators with underscore
    name = name.replace("/", "_")
    name = name.replace("-", "_")
    name = name.replace(" ", "_")

    # Remove special characters (keep only alphanumeric + underscore)
    name = re.sub(r'[^a-z0-9_]', '', name)

    # Remove consecutive underscores
    name = re.sub(r'_+', '_', name)

    # Truncate to max_length
    if len(name) > max_length:
        name = name[:max_length]

    # Remove trailing underscore if present after truncation
    name = name.rstrip('_')

    return name
