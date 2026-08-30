"""
FiniexTestingIDE - Report Artifact Errors
Raised when a persisted run artifact exists but cannot be read.
"""

from python.framework.exceptions.finiex_error import FiniexError


class ReportArtifactUnreadableError(FiniexError):
    """
    A run artifact is present but does not match the model that reads it.

    The usual cause is age: the artifact was written before a model field changed shape, and
    the project keeps no compatibility layer for run output (§27) — artifacts are development
    output and are rewritten by every run. What must NOT happen is that this surfaces as an
    unexplained server error, so the read path names the condition instead of letting a
    validation failure escape.
    """

    def __init__(self, artifact: str, path: str, reason: str):
        """
        Args:
            artifact: The artifact's file name
            path: Where it was read from
            reason: The underlying parse failure
        """
        self.artifact = artifact
        self.path = path
        self.reason = reason
        super().__init__(
            f"Run artifact '{artifact}' at {path} cannot be read with the current model — "
            f'it was most likely written by an older schema. Re-run to regenerate it. '
            f'Cause: {reason}')
