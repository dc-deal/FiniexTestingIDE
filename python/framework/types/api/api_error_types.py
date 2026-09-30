"""
FiniexTestingIDE - API Error Types

The shape of one entry in the API's error vocabulary — the catalog itself is
`python/api/api_error_catalog.py`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiErrorKind:
    """
    One error the API can answer with: its HTTP status, its machine code and its sentence.

    The code is the CAUSE, never only the status — a consumer renders `run_not_completed` and
    `artifact_not_produced` differently, and a bare `not_found` gives it nothing to render.

    Args:
        status: HTTP status
        code: Machine-readable cause, carried as `error` in the body
        message: The sentence carried as `detail` — a `str.format` template whose placeholders
            the raising route fills
    """
    status: int
    code: str
    message: str
