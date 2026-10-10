"""
FiniexTestingIDE - Atomic File Utils
Write a file so a reader never sees half of it.

A temporary file beside the target, then a rename: the rename replaces the target in one
step, so a reader opens either the old file or the new one. The temporary name is unique per
writer, so two writers of one target cannot rename each other's half-written file into place.
"""

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """
    Write a text file atomically.

    Args:
        path: The file to write; its directory is created when missing
        text: The full content, written as UTF-8

    Returns:
        None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
        # mkstemp makes the file readable by its owner only; the file it replaces got the
        # process's usual mode, and so does this one
        os.chmod(temporary, 0o666 & ~_current_umask())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _current_umask() -> int:
    """
    The process's file-creation mask. Python can only read it by setting it, so it is set and
    restored at once.

    Returns:
        The mask, e.g. 0o022
    """
    mask = os.umask(0)
    os.umask(mask)
    return mask
