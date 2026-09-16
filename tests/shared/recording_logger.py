"""
FiniexTestingIDE - Recording Logger for Tests

The project's loggers do not route through the stdlib `logging` module, so pytest's
`caplog` sees nothing they write. Where a test has to assert what was REPORTED — and
at which level — it injects this stand-in instead.

That case is not cosmetic: a finding whose whole subject is that a failure reported
into a channel nobody reads can only be pinned by asserting the report exists.

Convention: not a pytest fixture — construct it in the test and pass it in.
"""

from typing import List, Tuple


class RecordingLogger:
    """Minimal AbstractLogger stand-in that keeps what was said, at which level."""

    def __init__(self):
        self.lines: List[Tuple[str, str]] = []

    def verbose(self, message: str) -> None:
        self.lines.append(('verbose', message))

    def debug(self, message: str) -> None:
        self.lines.append(('debug', message))

    def info(self, message: str) -> None:
        self.lines.append(('info', message))

    def warning(self, message: str) -> None:
        self.lines.append(('warning', message))

    def error(self, message: str) -> None:
        self.lines.append(('error', message))

    def levels(self) -> List[str]:
        """All recorded levels, in order.

        Returns:
            Level names, one per recorded line
        """
        return [level for level, _ in self.lines]

    def text(self) -> str:
        """All recorded messages joined, regardless of level.

        Returns:
            One string with every message on its own line
        """
        return '\n'.join(message for _, message in self.lines)

    def text_at(self, level: str) -> str:
        """The messages recorded at one level.

        Args:
            level: Level name, e.g. 'error'

        Returns:
            One string with every message of that level on its own line
        """
        return '\n'.join(message for lvl, message in self.lines if lvl == level)
