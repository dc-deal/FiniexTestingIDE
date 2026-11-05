"""
A logger that drops everything — for tests that build a portfolio or an executor directly and
assert on nothing it logs.
"""


class NullLogger:
    """Takes every logging call a framework unit makes and keeps none of it."""

    def verbose(self, *args, **kwargs) -> None:
        """Drop a verbose line."""

    def debug(self, *args, **kwargs) -> None:
        """Drop a debug line."""

    def info(self, *args, **kwargs) -> None:
        """Drop an info line."""

    def warning(self, *args, **kwargs) -> None:
        """Drop a warning."""

    def error(self, *args, **kwargs) -> None:
        """Drop an error."""
