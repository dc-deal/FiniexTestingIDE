"""
Acceptance study errors (#332, #358).

A certificate reads a capture as evidence, so a capture it cannot read is refused by name — the run,
the file and what is wrong with it — rather than crashing or reading as an empty study.
"""

from pathlib import Path

from python.framework.exceptions.finiex_error import FiniexError


class AcceptanceCaptureUnreadableError(FiniexError, ValueError):
    """A study's capture is empty, or a line before its last cannot be read."""

    def __init__(self, capture_path: Path, reason: str):
        self.capture_path = capture_path
        self.reason = reason
        super().__init__(f'The capture {capture_path} cannot be read as evidence: {reason}')
