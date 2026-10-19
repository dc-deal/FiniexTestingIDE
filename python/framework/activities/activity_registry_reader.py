"""
FiniexTestingIDE - Activity Registry Reader
The reading side of the list of what runs on this machine (#603).

An entry is listed while its lock is held. An entry whose lock is free belongs to an activity that
ended without leaving — a process killed by `kill -9`, a machine that lost power — and the reader
removes it. It removes one only when the lock is free AND the process the entry names is gone, so a
mount or a container on which the lock cannot be seen does not empty the list. A lock file with no
entry beside it is an activity in the moment between taking its lock and writing its entry; it is
removed only once it is older than a minute and free.

A reading removes nothing but the files of activities that have ended.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.activity_registry_types import ActivityEntry, ActivityListing
from python.framework.utils.file_lock import is_lock_held
from python.framework.utils.process_memory_reader import read_process_start

# How long a lock file may stand without its entry before a reader counts it as left behind.
_ORPHAN_LOCK_AGE_S = 60.0


class ActivityRegistryReader:
    """
    Reads the running activities, and clears what ended without leaving.
    """

    def __init__(self, directory: Optional[Path] = None):
        """
        Args:
            directory: The registry directory; the configured one when omitted
        """
        self._directory = (directory if directory is not None
                           else AppConfigManager().get_activity_registry_path())

    def read(self) -> ActivityListing:
        """
        One reading of the registry, newest activity first.

        A missing directory is an empty list — nothing has registered yet. A directory that exists
        and cannot be listed raises: a list that cannot be read is not a list of nothing.

        Returns:
            The running activities and the ids of held entries this code cannot read
        """
        listing = ActivityListing()
        if not self._directory.is_dir():
            return listing
        names = os.listdir(self._directory)
        entries = {name[:-len('.json')] for name in names
                   if name.endswith('.json') and not name.startswith('.')}
        locks = {name[:-len('.lock')] for name in names if name.endswith('.lock')}

        for activity_id in sorted(entries):
            entry = self._load(activity_id)
            if is_lock_held(self._lock_path(activity_id)):
                if entry is not None:
                    listing.entries.append(entry)
                elif self._json_path(activity_id).exists():
                    listing.unreadable_activity_ids.append(activity_id)
                continue
            if entry is not None and _process_lives(entry):
                listing.entries.append(entry)
                continue
            self._remove(activity_id)

        for activity_id in locks - entries:
            self._remove_orphan_lock(activity_id)

        listing.entries.sort(key=lambda entry: entry.started_at, reverse=True)
        return listing

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
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _remove(self, activity_id: str) -> None:
        """
        Remove the files of an activity that ended without leaving.

        Args:
            activity_id: The activity
        """
        self._json_path(activity_id).unlink(missing_ok=True)
        self._lock_path(activity_id).unlink(missing_ok=True)

    def _remove_orphan_lock(self, activity_id: str) -> None:
        """
        Remove a lock file with no entry, once it is old enough and nobody holds it.

        Args:
            activity_id: The activity the lock file is named for
        """
        lock_path = self._lock_path(activity_id)
        try:
            age = time.time() - lock_path.stat().st_mtime
        except FileNotFoundError:
            return
        if age > _ORPHAN_LOCK_AGE_S and not is_lock_held(lock_path):
            lock_path.unlink(missing_ok=True)

    def _json_path(self, activity_id: str) -> Path:
        return self._directory / f'{activity_id}.json'

    def _lock_path(self, activity_id: str) -> Path:
        return self._directory / f'{activity_id}.lock'


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
