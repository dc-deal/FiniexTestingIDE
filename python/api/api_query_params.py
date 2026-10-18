"""
Query parameters every route reads the same way.

A timestamp in a query string is read here and nowhere else. Declared as a `datetime` parameter,
FastAPI reads it in lax mode, where a string of digits is Unix seconds: `since=2026` became
1970-01-01T00:33:46Z, and a filter that silently passed everything looked like one that works.
"""

from datetime import datetime
from typing import Optional

from python.api.api_error_catalog import INVALID_TIMESTAMP, api_error


def parse_iso_query(value: Optional[str], field: str) -> Optional[datetime]:
    """
    Read an ISO-8601 query parameter, or refuse it with a 400.

    Args:
        value: The raw parameter, None when absent
        field: The parameter's name, for the error

    Returns:
        The instant as written (naive when it names no offset), None when absent
    """
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise api_error(INVALID_TIMESTAMP, field=field, value=value)
