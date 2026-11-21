"""
Tick index types — how one build of the tick index went: an update that scanned only the files
that changed, or a full rebuild that scanned every file, and why.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TickIndexMethod(Enum):
    """How the tick index was brought up to the tick files on disk."""
    UPDATE = 'update'
    FULL_REBUILD = 'full_rebuild'


class TickIndexRebuildReason(Enum):
    """Why a build scanned every tick file instead of only the ones that changed."""
    ASKED_FOR = 'asked_for'
    NO_INDEX = 'no_index'
    OLDER_SCHEMA = 'older_schema'
    NO_FILE_IDENTITIES = 'no_file_identities'
    NEWER_FILES = 'newer_files'


@dataclass(frozen=True)
class TickIndexRun:
    """
    One build of the tick index.

    Args:
        method: Update or full rebuild
        rebuild_reason: Why every file was scanned; None for an update
        scanned: Files read in full by this build
        unchanged: Entries an update kept because their file had not changed; None for a full
            rebuild, which keeps none
        gone: Entries an update dropped because their file no longer exists; None for a full
            rebuild, which does not compare
        elapsed_s: How long the build took
    """
    method: TickIndexMethod
    rebuild_reason: Optional[TickIndexRebuildReason]
    scanned: int
    unchanged: Optional[int]
    gone: Optional[int]
    elapsed_s: float
