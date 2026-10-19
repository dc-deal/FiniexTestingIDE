"""
FiniexTestingIDE - Broker Config Cache Policy

When a cached venue specification is fresh enough to use, asked of the symbol's OWN entry: the
cache file is rewritten by any symbol's refresh, so its age says nothing about one symbol (#608).

Framework-side on purpose. The rule is the same for every venue, and #328 moves each venue's
fetcher into a package of its own — a rule living inside one of them would be copied by the next.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from python.framework.utils.time_utils import ensure_utc_aware

# An entry older than this is refreshed from the venue before it is used. Read by the fetchers,
# and by the backtest's post-run check, which reports a run that read an older entry.
CACHE_REFRESH_DAYS = 7

# An entry older than this, used because its refresh failed, warns loudly rather than quietly.
CACHE_STALE_WARN_DAYS = 30


def get_entry_fetched_at(entry: Optional[Dict[str, Any]]) -> Optional[datetime]:
    """
    When one symbol's cache entry was last fetched from the venue, from its own `_last_fetched`.

    Args:
        entry: The symbol's entry in the cache (None when absent)

    Returns:
        The UTC instant, or None when the entry is absent or carries no readable stamp
    """
    if not entry:
        return None
    stamp = entry.get('_last_fetched')
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        # The writer always appends the Z; a stamp without an offset comes only from a hand
        # edit, and is read as UTC like every naive time in this project
        return ensure_utc_aware(datetime.fromisoformat(stamp.replace('Z', '+00:00')))
    except ValueError:
        return None


def entry_age_days(entry: Optional[Dict[str, Any]]) -> Optional[float]:
    """
    Age in days of one symbol's cache entry.

    Args:
        entry: The symbol's entry in the cache (None when absent)

    Returns:
        Age in days, or None when the entry is absent or carries no readable stamp — both
        read as "not known to be fresh"
    """
    fetched_at = get_entry_fetched_at(entry)
    if fetched_at is None:
        return None
    return (datetime.now(timezone.utc) - fetched_at).total_seconds() / 86400.0
