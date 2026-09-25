"""
Run patch store errors (#551).

Both are integrity failures of a content-addressed store, and both are loud on purpose: the key
IS the content, so a patch that does not hash to its key is not a slightly wrong record — it is
a record of code that did not run.
"""

from pathlib import Path

from python.framework.exceptions.finiex_error import FiniexError


class RunPatchHashMismatchError(FiniexError, ValueError):
    """A patch was handed in under a hash it does not have — refused before anything is written."""

    def __init__(self, patch_hash: str, actual: str):
        """
        Args:
            patch_hash: The hash the caller filed the patch under
            actual: The SHA256 the patch bytes actually have
        """
        self.patch_hash = patch_hash
        self.actual = actual
        super().__init__(
            f"Run patch refused: filed under '{patch_hash}', but its bytes hash to '{actual}'. "
            f"A content-addressed entry whose name is not its content would tie a run to code "
            f"it never ran — the caller has hashed something other than what it stores."
        )


class RunPatchCorruptError(FiniexError, RuntimeError):
    """A stored patch no longer hashes to its own name."""

    def __init__(self, patch_hash: str, path: Path, actual: str):
        """
        Args:
            patch_hash: The hash the entry is filed under
            path: Where the entry lies
            actual: The SHA256 its bytes have now
        """
        self.patch_hash = patch_hash
        self.path = path
        self.actual = actual
        super().__init__(
            f"Run patch {path} is damaged: its bytes hash to '{actual}', not to the "
            f"'{patch_hash}' it is filed under. It is not served, because a patch that differs "
            f"from its name restores code that never ran. The next run from the same tree "
            f"rewrites it from verified bytes."
        )
