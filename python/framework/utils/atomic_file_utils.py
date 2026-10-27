"""
FiniexTestingIDE - Atomic File Utils
Write a file so a reader never sees half of it.

A temporary file beside the target, then a rename: the rename replaces the target in one
step, so a reader opens either the old file or the new one. The temporary name is unique per
writer, so two writers of one target cannot rename each other's half-written file into place.

A durable write also survives a crash of the machine, not only of the process: the file's bytes
are forced to the disk before the rename, and the rename itself after it. Without that, a file
renamed to a NEW name can come back empty after a power loss — the filesystem commits the rename
before the bytes — while whatever the caller deletes or replaces next is already gone.
"""

import functools
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

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
    write_file_atomic(path, lambda temporary: temporary.write_text(text, encoding='utf-8'), mode)


def write_file_atomic(path: Path, write: Callable[[Path], None],
                      mode: Optional[int] = None, durable: bool = False) -> None:
    """
    Write a file of any format atomically: the writer fills a temporary file beside the target,
    which then replaces it.

    Args:
        path: The file to write; its directory is created when missing
        write: Writes the full content into the path it is given
        mode: The file's permissions; the process's usual mode when omitted
        durable: Force the bytes and the rename to the disk, for a record a crash of the machine
            must not take — a sealed trading day, a ledger fragment

    Returns:
        None
    """
    create_directory(path.parent, holds=f'the file {path.name}')
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    os.close(handle)
    try:
        write(Path(temporary))
        # mkstemp makes the file readable by its owner only; the file it replaces got the
        # process's usual mode, and so does this one unless the caller names another
        os.chmod(temporary, mode if mode is not None else 0o666 & ~_current_umask())
        if durable:
            _sync_file(Path(temporary))
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    if durable:
        _sync_folder(path.parent)


def _sync_file(path: Path) -> None:
    """
    Force a file's bytes to the disk.

    Args:
        path: The file
    """
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_folder(folder: Path) -> None:
    """
    Force a folder's entries — a rename into it — to the disk. Some filesystems refuse to sync a
    folder, network and container mounts among them; there the file's own sync is what was
    possible, and the write stands.

    Args:
        folder: The folder
    """
    try:
        descriptor = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


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
