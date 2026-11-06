"""
FiniexTestingIDE - Tick Inbox Conveyor
Where a raw file leaves the tick import inbox for (#120): the finished directory when the import
took it, the refused directory when it refused or failed on it — a loose export and a zip alike.

Every import drains the inbox this way, so the inbox only ever holds new work: a file left there
would be read again by every import, and one thrown away would hide the problem (the Invalid
Message Channel of Hohpe and Woolf, *Enterprise Integration Patterns*). A refused file keeps its
content unchanged, the raw source of truth it is, with a note of why beside it; it goes back to
the inbox by hand once its cause is fixed.

Neither directory is ever overwritten: an identical file there already makes the inbox copy
redundant, a different one of the same name keeps the file in the inbox.
"""

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from python.framework.types.import_types.tick_inbox_types import (
    InboxDisposition,
    InboxMove,
)
from python.framework.utils.file_utils import create_directory

# The note beside a refused file: its own name plus this suffix, appended to on every refusal
REFUSAL_NOTE_SUFFIX = '.refused.txt'

_HASH_CHUNK_BYTES = 1 << 20


class TickInboxConveyor:
    """
    Moves files and zips out of the tick import inbox, never overwriting what lies at the
    destination.
    """

    def __init__(self, finished_dir: Optional[Path], refused_dir: Optional[Path]) -> None:
        """
        Args:
            finished_dir: Where taken files go, None when files stay in the inbox
            refused_dir: Where refused files go, None when files stay in the inbox
        """
        self._finished_dir = finished_dir
        self._refused_dir = refused_dir

    def get_refused_dir(self) -> Optional[Path]:
        """
        Where refused files go.

        Returns:
            The refused directory, None when files stay in the inbox
        """
        return self._refused_dir

    def to_finished(self, path: Path) -> InboxMove:
        """
        Move a file the import took to the finished directory.

        Args:
            path: The file or zip

        Returns:
            Where it went
        """
        return _move_without_overwrite(path, self._finished_dir,
                                       InboxDisposition.MOVED_TO_FINISHED,
                                       holds='the raw files after their import')

    def to_refused(self, path: Path, reason: str) -> InboxMove:
        """
        Move a file the import refused or failed on to the refused directory, and append the
        reason to the note beside it — also when an identical file lay there already, so the
        note keeps every refusal.

        Args:
            path: The file or zip
            reason: Why it was refused

        Returns:
            Where it went
        """
        move = _move_without_overwrite(path, self._refused_dir,
                                       InboxDisposition.MOVED_TO_REFUSED,
                                       holds='the raw files the import refused')
        if move.disposition is not InboxDisposition.KEPT_IN_INBOX:
            note = self._refused_dir / f'{path.name}{REFUSAL_NOTE_SUFFIX}'
            # When WE wrote the note — an observation, not an event time
            stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
            # One line per refusal: a validation message spans several
            text = ' | '.join(line.strip() for line in reason.splitlines() if line.strip())
            with open(note, 'a', encoding='utf-8') as stream:
                stream.write(f'{stamp} · {text}\n')
        return move


    def move_beside(self, path: Path, move: InboxMove) -> InboxMove:
        """
        Move a file that belongs to a zip — its package manifest — to where the zip went,
        without a note of its own. Nothing moves when the zip stayed in the inbox.

        Args:
            path: The file, which need not exist
            move: Where the zip went

        Returns:
            Where the file went
        """
        if (not path.exists() or move.destination is None
                or move.disposition is InboxDisposition.KEPT_IN_INBOX):
            return InboxMove(disposition=InboxDisposition.KEPT_IN_INBOX)
        moved = (InboxDisposition.MOVED_TO_REFUSED
                 if move.disposition is InboxDisposition.MOVED_TO_REFUSED
                 else InboxDisposition.MOVED_TO_FINISHED)
        return _move_without_overwrite(path, move.destination, moved,
                                       holds='the raw files after their import')


def _move_without_overwrite(path: Path, target_dir: Optional[Path],
                            moved: InboxDisposition, holds: str) -> InboxMove:
    """
    Move a file into a directory unless a different file of its name lies there.

    Args:
        path: The file
        target_dir: The directory, None when moving is off
        moved: The disposition a completed move reports
        holds: What the directory holds, for the message should it not be creatable

    Returns:
        Where the file went
    """
    if target_dir is None:
        return InboxMove(disposition=InboxDisposition.KEPT_IN_INBOX,
                         detail='moving imported files is switched off (move_processed_files)')
    target = target_dir / path.name
    if target.exists():
        if file_sha256(target) == file_sha256(path):
            path.unlink()
            return InboxMove(disposition=InboxDisposition.ALREADY_THERE, destination=target_dir)
        return InboxMove(disposition=InboxDisposition.KEPT_IN_INBOX, destination=target_dir,
                         detail=f'a different {path.name} lies in {target_dir} — it is never '
                                'overwritten')
    create_directory(target_dir, holds=holds)
    # A rename where both lie on one disk, a copy and delete where they do not
    shutil.move(str(path), str(target))
    return InboxMove(disposition=moved, destination=target_dir)


def file_sha256(path: Path) -> str:
    """
    A file's SHA-256.

    Args:
        path: The file

    Returns:
        The hex digest
    """
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(_HASH_CHUNK_BYTES), b''):
            digest.update(chunk)
    return digest.hexdigest()
