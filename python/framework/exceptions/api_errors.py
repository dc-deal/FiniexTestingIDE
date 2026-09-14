"""
FiniexTestingIDE - API Errors
Exception type for the read-only HTTP API layer.
"""

from typing import Dict, Optional

from python.framework.exceptions.finiex_error import FiniexError


class ApiException(FiniexError):
    """
    Raised by endpoint handlers to produce a structured JSON error response.

    Response body: {"error": "<error>", "detail": "<detail>"}

    Some answers are only well-formed with a header. A 401 carries
    `WWW-Authenticate: Bearer` (RFC 7235) — that is how a client tells a dead credential from a
    transport failure, which is the same conversion §43 forbids one layer down; a rate-limited
    429 carries `Retry-After`. Everything else passes None and nothing changes.

    Args:
        status_code: HTTP status
        error: Short machine-readable code carried in the body
        detail: Human-readable sentence
        headers: Response headers this answer needs to be actionable, or None
    """

    def __init__(self, status_code: int, error: str, detail: str,
                 headers: Optional[Dict[str, str]] = None):
        self.status_code = status_code
        self.error = error
        self.detail = detail
        self.headers = headers


class ApiConfigurationError(FiniexError, ValueError):
    """
    Raised at boot when the API's own configuration cannot be trusted.

    Separate from ApiException because it never becomes a response: it happens before the
    server serves anything, and the right outcome is a refusal to start rather than an error
    body. A ValueError as well, so a caller already catching configuration problems keeps
    working.
    """
