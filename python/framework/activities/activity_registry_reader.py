"""
FiniexTestingIDE - Activity Registry Reader
The reading side of the list of what runs on this machine (#603).

An entry is listed while its lock is held. An entry whose lock is free belongs to an activity that
ended without leaving — a process killed by `kill -9`, a machine that lost power — and the reader
removes it. It removes a readable one only when the lock is free AND the process the entry names is
gone, so a mount or a container on which the lock cannot be seen does not empty the list; an entry
this code cannot read is removed once its lock is free. A lock file with no entry beside it is an
activity in the moment between taking its lock and writing its entry, and the temporary file of an
entry being written is one in the moment before it is renamed; both are removed only once they are
older than a minute and free.

A reading touches nothing but files named by an activity id, and removes nothing but those of
activities that have ended. One entry it cannot judge — its lock cannot be asked about — is named
as unreadable and left alone; a removal that fails is left for a later reading. Only a directory
that cannot be listed fails the reading as a whole.
"""

import errno
import json
import os
import time
from pathlib import Path
from typing import Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.activity_registry_types import (
    ACTIVITY_ID_PREFIX,
    ActivityEntry,
    ActivityListing,
)
from python.framework.utils.file_lock import is_lock_held
from python.framework.utils.process_memory_reader import read_process_start

# How long a lock file may stand without its entry, or an entry's temporary file without its
# rename, before a reader counts it as left behind.
_ORPHAN_LOCK_AGE_S = 60.0

# The temporary file an entry is written to before its rename (utils/atomic_file_utils): the
# entry's name behind a dot, and a random part.
_TEMPORARY_PREFIX = f'.{ACTIVITY_ID_PREFIX}'


class ActivityRegistryReader:
    """
    Reads the running activities, and clears what ended without leaving.
    """

    def __init__(self, directory: Optional[Path] = None):
        """
        Args:
            directory: The registry directory; the configured one when omitted, resolved at the
                first reading
        """
        self._directory = directory

    def read(self) -> ActivityListing:
        """
        One reading of the registry, newest activity first.

        A missing directory is an empty list — nothing has registered yet. A directory that exists
        and cannot be listed raises, and so does a path that is no directory or a directory that
        cannot be resolved: a list that cannot be read is not a list of nothing.

        Returns:
            The running activities and the ids of entries this code cannot read or judge
        """
        listing = ActivityListing()
        directory = self._resolve_directory()
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            return listing
        entries = {name[:-len('.json')] for name in names
                   if name.startswith(ACTIVITY_ID_PREFIX) and name.endswith('.json')}
        locks = {name[:-len('.lock')] for name in names
                 if name.startswith(ACTIVITY_ID_PREFIX) and name.endswith('.lock')}

        for activity_id in sorted(entries):
            try:
                self._read_entry(activity_id, listing)
            except OSError:
                # Its lock cannot be asked about, so whether it runs is unknown: named, never removed
                listing.unreadable_activity_ids.append(activity_id)

        for activity_id in locks - entries:
            self._remove_left_behind(self._lock_path(activity_id))
        for name in names:
            if name.startswith(_TEMPORARY_PREFIX) and name.endswith('.tmp'):
                self._remove_left_behind(directory / name)

        listing.entries.sort(key=lambda entry: entry.started_at, reverse=True)
        return listing

    def _resolve_directory(self) -> Path:
        """
        The registry directory, resolved once.

        Returns:
            The directory; one that cannot be resolved — no home directory to expand `~` in —
            raises as an OSError, which a reader of the list reports as a list that cannot be read
        """
        if self._directory is None:
            directory = AppConfigManager().get_activity_registry_path()
            if not directory.is_absolute():
                raise OSError(errno.ENOENT, 'the activity directory cannot be resolved: '
                                            'no home directory')
            self._directory = directory
        return self._directory

    def _read_entry(self, activity_id: str, listing: ActivityListing) -> None:
        """
        Judge one entry: listed, named as unreadable, or removed as ended.

        Args:
            activity_id: The activity
            listing: The reading it is added to
        """
        entry = self._load(activity_id)
        if is_lock_held(self._lock_path(activity_id)):
            if entry is not None:
                listing.entries.append(entry)
            elif self._json_path(activity_id).exists():
                listing.unreadable_activity_ids.append(activity_id)
            return
        if entry is not None and _process_lives(entry):
            listing.entries.append(entry)
            return
        self._remove(activity_id)

    def _load(self, activity_id: str) -> Optional[ActivityEntry]:
        """
        An entry from its file.

        Args:
            activity_id: The activity

        Returns:
            The entry, or None when the file is gone or cannot be read by this code
        """
        try:
            raw = json.loads(self._json_path(activity_id).read_text(encoding='utf-8'))
            return ActivityEntry.from_dict(raw)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
            return None

    def _remove(self, activity_id: str) -> None:
        """
        Remove the files of an activity that ended without leaving; a removal that fails is left
        for a later reading.

        Args:
            activity_id: The activity
        """
        try:
            self._json_path(activity_id).unlink(missing_ok=True)
            self._lock_path(activity_id).unlink(missing_ok=True)
        except OSError:
            pass

    def _remove_left_behind(self, path: Path) -> None:
        """
        Remove a lock file with no entry, or an entry's temporary file, once it is old enough and
        nobody holds it; a removal that fails is left for a later reading.

        Args:
            path: The file
        """
        try:
            age = time.time() - path.stat().st_mtime
            if age > _ORPHAN_LOCK_AGE_S and not is_lock_held(path):
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def _json_path(self, activity_id: str) -> Path:
        """
        An activity's entry file.

        Args:
            activity_id: The activity

        Returns:
            Its path in the registry directory
        """
        return self._resolve_directory() / f'{activity_id}.json'

    def _lock_path(self, activity_id: str) -> Path:
        """
        An activity's lock file.

        Args:
            activity_id: The activity

        Returns:
            Its path in the registry directory
        """
        return self._resolve_directory() / f'{activity_id}.lock'


def _process_lives(entry: ActivityEntry) -> bool:
    """
    Whether the process an entry names still runs — the same process, not a later one under its id.

    Args:
        entry: The entry

    Returns:
        True when a process with the entry's id and start time exists
    """
    started = read_process_start(entry.pid)
    return started is not None and abs(started - entry.process_started_at) < 0.01
