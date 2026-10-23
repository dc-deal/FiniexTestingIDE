"""
FiniexTestingIDE - Atomic File Utils
Write a file so a reader never sees half of it.

A temporary file beside the target, then a rename: the rename replaces the target in one
step, so a reader opens either the old file or the new one. The temporary name is unique per
writer, so two writers of one target cannot rename each other's half-written file into place.
"""

import functools
import os
import tempfile
from pathlib import Path
from typing import Optional

from python.framework.utils.file_utils import create_directory


def write_text_atomic(path: Path, text: str, mode: Optional[int] = None) -> None:
    """
    Write a text file atomically.

    Args:
        path: The file to write; its directory is created when missing
        text: The full content, written as UTF-8
        mode: The file's permissions; the process's usual mode when omitted

    Returns:
        None
    """
    create_directory(path.parent, holds=f'the file {path.name}')
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
        # mkstemp makes the file readable by its owner only; the file it replaces got the
        # process's usual mode, and so does this one unless the caller names another
        os.chmod(temporary, mode if mode is not None else 0o666 & ~_current_umask())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@functools.lru_cache(maxsize=1)
def _current_umask() -> int:
    """
    The process's file-creation mask. Python can only read it by setting it, so it is set and
    restored — once per process: for that instant the mask is 0 for every thread, and a file
    another thread creates then would be writable by anyone. Nothing in this process changes the
    mask afterwards.

    Returns:
        The mask, e.g. 0o022
    """
    mask = os.umask(0)
    os.umask(mask)
    return mask
