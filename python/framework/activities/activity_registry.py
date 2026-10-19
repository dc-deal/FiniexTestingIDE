"""
FiniexTestingIDE - Activity Registry
Every long-running activity enters itself here at its start and leaves at its end (#603).

One directory per machine, outside every code tree (`app_config.json::activities.directory`):
several checkouts and worktrees on one machine, and on a server one code tree per release tag, all
list their work in it. An entry is two files named by the activity's own id:

    <id>.lock   taken first and held for the activity's life (utils/file_lock)
    <id>.json   written after the lock is held, atomically

so an entry is visible only while its lock is held. At the end the JSON goes first, then the lock
file, then the lock — the same order backwards. Whether an activity runs is decided by its lock
alone, never by whether its file or its process id exists.

Registering never ends an activity — not even where the directory cannot be resolved, which is why
it is resolved inside the guarded step rather than when the registry is built. An entry that cannot
be written costs a warning in the log the activity hands over, the global log where it hands none,
and the activity runs on unlisted: an observability fault must not stop a backtest, and must never
end an unattended session with an exit code no supervisor restarts.
"""

import errno
import json
import os
import secrets
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

import psutil

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.activity_registry_types import (
    ACTIVITY_ID_PREFIX,
    ActivityEntry,
    ActivityKind,
    ActivityState,
)
from python.framework.types.run_origin_types import RunChannel, RunOrigin
from python.framework.utils.atomic_file_utils import write_text_atomic
from python.framework.utils.file_lock import FileLock
from python.framework.utils.git_info_utils import get_framework_root, get_git_commit
from python.framework.utils.run_origin_builder import build_run_origin

# The activities this process is inside right now, innermost last. A forked child inherits the
# list, so an activity it registers names the one that forked it as its parent.
_active_activity_ids: List[str] = []


@dataclass
class _Registration:
    """An entry this process holds: its id, its files and its lock."""
    activity_id: str
    lock: FileLock
    json_path: Path


class ActivityRegistry:
    """
    The writing side of the list of what runs on this machine.
    """

    def __init__(self, directory: Optional[Path] = None):
        """
        Args:
            directory: The registry directory; the configured one when omitted, resolved only
                when an entry is written
        """
        self._config = AppConfigManager()
        self._directory = directory
        self._session_env_var = self._config.get_activity_session_env_var()

    @contextmanager
    def running(
        self,
        kind: ActivityKind,
        label: str,
        run_id: Optional[str] = None,
        sweep_id: Optional[str] = None,
        origin: Optional[RunOrigin] = None,
        channel: RunChannel = RunChannel.DIRECT,
        logger: Optional[AbstractLogger] = None,
    ) -> Iterator[Optional[str]]:
        """
        Hold an entry for the duration of a `with` block.

        Args:
            kind: What kind of work this is
            label: What it is about, for a person — a scenario set, a profile, a sweep
            run_id: The run it writes, where it writes one
            sweep_id: The sweep it is, where it is one
            origin: Who started it, where the activity already knows — a run's own origin
            channel: How it was started, as its entry point declares it; used to build the
                origin when none is given
            logger: Where a failed registration is reported; the global log when omitted

        Returns:
            The activity id while the block runs, or None when no entry could be written
        """
        registration = self._register(kind, label, run_id, sweep_id, origin, channel, logger)
        if registration is None:
            yield None
            return
        _active_activity_ids.append(registration.activity_id)
        try:
            yield registration.activity_id
        finally:
            if registration.activity_id in _active_activity_ids:
                _active_activity_ids.remove(registration.activity_id)
            self._unregister(registration, logger)

    def _register(
        self,
        kind: ActivityKind,
        label: str,
        run_id: Optional[str],
        sweep_id: Optional[str],
        origin: Optional[RunOrigin],
        channel: RunChannel,
        logger: Optional[AbstractLogger],
    ) -> Optional[_Registration]:
        """
        Take the lock, then write the entry.

        Args:
            kind: What kind of work this is
            label: What it is about
            run_id: The run it writes, if any
            sweep_id: The sweep it is, if any
            origin: Who started it, if the caller knows
            channel: How it was started, for an origin built here
            logger: Where a failure is reported

        Returns:
            The registration, or None when it could not be made
        """
        lock: Optional[FileLock] = None
        json_path: Optional[Path] = None
        try:
            # Resolved here, inside the guard: a home directory that cannot be resolved costs the
            # warning below, never the activity
            directory = (self._directory if self._directory is not None
                         else self._config.get_activity_registry_path())
            if not directory.is_absolute():
                raise OSError(errno.ENOENT, f'the activity directory {directory} cannot be resolved')
            started_at = datetime.now(timezone.utc)
            activity_id = (f"{ACTIVITY_ID_PREFIX}{started_at.strftime('%Y%m%d_%H%M%S')}_"
                           f'{secrets.token_hex(4)}')
            lock = FileLock(directory / f'{activity_id}.lock')
            entry = ActivityEntry(
                activity_id=activity_id,
                activity_kind=kind,
                activity_state=ActivityState.RUNNING,
                activity_label=label,
                pid=os.getpid(),
                process_started_at=psutil.Process().create_time(),
                command_line=list(sys.argv),
                started_at=started_at,
                started_monotonic=time.monotonic(),
                working_tree=code_tree_of_this_process(),
                console_session=(os.environ.get(self._session_env_var, '')
                                 if self._session_env_var else ''),
                git_commit=get_git_commit(),
                origin=origin if origin is not None else _origin_for(channel),
                run_id=run_id,
                sweep_id=sweep_id,
                parent_activity_id=_active_activity_ids[-1] if _active_activity_ids else None,
            )
            json_path = directory / f'{activity_id}.json'
            write_text_atomic(json_path, json.dumps(entry.to_dict(), indent=2))
            return _Registration(activity_id=activity_id, lock=lock, json_path=json_path)
        except BaseException as error:
            # A stop (Ctrl+C) between the lock and the entry leaves neither behind: the stop goes
            # on, and the list does not show an activity that never started.
            _discard(lock, json_path)
            if not isinstance(error, Exception):
                raise
            # Never fatal, by design: the activity runs on, unlisted, and the reason is said.
            _warn(logger, f'⚠️ Activity not listed — {kind.value} "{label}": '
                          f'{type(error).__name__}: {error}')
            return None

    def _unregister(self, registration: _Registration, logger: Optional[AbstractLogger]) -> None:
        """
        Remove the entry, then its lock file, then give the lock up.

        Args:
            registration: The entry this process holds
            logger: Where a failure is reported
        """
        try:
            registration.json_path.unlink(missing_ok=True)
            registration.lock.get_path().unlink(missing_ok=True)
        except OSError as error:
            # The lock below is released either way; a file left behind is cleared by a reader
            # once this process has ended — until then it reads as the process's own.
            _warn(logger, f'⚠️ Activity entry {registration.activity_id} not removed: {error}')
        finally:
            try:
                registration.lock.release()
            except OSError as error:
                _warn(logger, f'⚠️ Activity lock {registration.activity_id} not released: {error}')


def _discard(lock: Optional[FileLock], json_path: Optional[Path]) -> None:
    """
    Take back a registration that did not complete: its entry, its lock file and its lock.

    Args:
        lock: The lock, when it was taken
        json_path: The entry, when its path was named
    """
    if lock is None:
        return
    try:
        if json_path is not None:
            json_path.unlink(missing_ok=True)
        lock.get_path().unlink(missing_ok=True)
    except OSError:
        pass  # what stays behind is cleared by a reader once the lock is free
    finally:
        try:
            lock.release()
        except OSError:
            pass  # the lock ends with the process at the latest


def code_tree_of_this_process() -> str:
    """
    The code tree this process runs from, as an entry records it and a reader compares it.

    Returns:
        The repository root git reports, or this checkout's root where git gives no answer
    """
    return get_framework_root() or str(Path(__file__).resolve().parents[3])


def _origin_for(channel: RunChannel) -> Optional[RunOrigin]:
    """
    The origin of an activity that states only its channel.

    Args:
        channel: How the activity was started

    Returns:
        The origin, or None when the installation's identity cannot be read — the entry is
        written without it rather than not at all
    """
    try:
        return build_run_origin(channel)
    except Exception:
        return None


def _warn(logger: Optional[AbstractLogger], message: str) -> None:
    """
    Report a registry fault where the activity's own warnings go.

    Args:
        logger: The activity's logger; the global log when None
        message: What happened
    """
    (logger if logger is not None else get_global_logger()).warning(message)
