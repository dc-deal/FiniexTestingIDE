"""
FiniexTestingIDE - API Errors
Exception type for the read-only HTTP API layer.
"""

from python.framework.exceptions.finiex_error import FiniexError


class ApiException(FiniexError):
    """
    Raised by endpoint handlers to produce a structured JSON error response.

    Response body: {"error": "<error>", "detail": "<detail>"}
    """

    def __init__(self, status_code: int, error: str, detail: str):
        self.status_code = status_code
        self.error = error
        self.detail = detail
