"""
FiniexTestingIDE - Activity Registry Types
What runs on this machine (#603): one entry per running activity, written by the activity and read
by the list.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, List, Optional, Self

from python.framework.types.machine_resources_types import FreeMemoryVerdict, MemoryReading
from python.framework.types.run_origin_types import RunOrigin

# The entry's own format. A reader meets an entry written by another code tree — another
# checkout or release on the same machine — and lists one it cannot read as unreadable rather
# than guessing at it.
ACTIVITY_ENTRY_SCHEMA = 1

# Every file of an entry is named by its activity id, and every id begins with this prefix. The
# reader takes only files whose names begin with it, so a directory that holds anything else keeps
# it.
ACTIVITY_ID_PREFIX = 'act_'

# The largest process id an entry may name — far above any kernel's limit, and small enough that no
# process-table call overflows on it.
_PID_CEILING = 2 ** 31 - 1


class ActivityKind(StrEnum):
    """What kind of work an activity is."""
    BACKTEST = 'backtest'
    SWEEP = 'sweep'
    AUTOTRADER_SESSION = 'autotrader_session'
    IMPORT = 'import'
    DISCOVERY_CACHE_BUILD = 'discovery_cache_build'
    TEST_SUITE = 'test_suite'
    API_SERVER = 'api_server'


class ActivityState(StrEnum):
    """
    Where an activity stands.

    Only RUNNING is written today: an entry exists while its activity runs. The other three belong
    to the scheduler that will write into the same list (#604).
    """
    QUEUED = 'queued'
    RUNNING = 'running'
    FINISHED = 'finished'
    FAILED = 'failed'


@dataclass
class ActivityEntry:
    """
    One running activity, as its entry on disk holds it.

    The key is `activity_id`, never the process id: an operating system reuses a process id,
    and two activities can run in one process — a sweep and the backtest of its current
    combination.
    """
    activity_id: str
    activity_kind: ActivityKind
    activity_state: ActivityState
    activity_label: str
    pid: int
    # When the process was started, as the operating system reports it — what tells a living
    # process from a later one under the same id.
    process_started_at: float
    command_line: List[str]
    started_at: datetime
    # The machine's monotonic clock at the start. Every process on one machine reads the same
    # clock, so the list measures an uptime that a stepped wall clock cannot falsify.
    started_monotonic: float
    working_tree: str
    console_session: str = ''
    git_commit: Optional[str] = None
    origin: Optional[RunOrigin] = None
    run_id: Optional[str] = None
    sweep_id: Optional[str] = None
    parent_activity_id: Optional[str] = None
    schema_version: int = ACTIVITY_ENTRY_SCHEMA

    def to_dict(self) -> Dict[str, Any]:
        """
        The entry as its JSON file holds it.

        Returns:
            A JSON-ready dict; enums as their values, the start as ISO-8601 UTC
        """
        return {
            'schema_version': self.schema_version,
            'activity_id': self.activity_id,
            'activity_kind': self.activity_kind.value,
            'activity_state': self.activity_state.value,
            'activity_label': self.activity_label,
            'pid': self.pid,
            'process_started_at': self.process_started_at,
            'command_line': list(self.command_line),
            'started_at': self.started_at.isoformat(),
            'started_monotonic': self.started_monotonic,
            'working_tree': self.working_tree,
            'console_session': self.console_session,
            'git_commit': self.git_commit,
            'origin': self.origin.model_dump(mode='json') if self.origin is not None else None,
            'run_id': self.run_id,
            'sweep_id': self.sweep_id,
            'parent_activity_id': self.parent_activity_id,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> Self:
        """
        An entry from its JSON file.

        Args:
            raw: The parsed file

        Returns:
            The entry; a missing field, or one of another type, raises — which the reader reports
            as unreadable rather than letting one foreign file fail the whole list
        """
        if not isinstance(raw, dict):
            raise ValueError('an activity entry is a JSON object')
        if raw.get('schema_version') != ACTIVITY_ENTRY_SCHEMA:
            raise ValueError(f"activity entry schema {raw.get('schema_version')!r} is not "
                             f'{ACTIVITY_ENTRY_SCHEMA}')
        origin = raw.get('origin')
        # Compared with other entries' starts when the list is sorted, so it must carry an offset
        started_at = datetime.fromisoformat(_text(raw, 'started_at'))
        if started_at.tzinfo is None:
            raise ValueError('an activity entry\'s started_at carries no offset')
        pid = raw['pid']
        if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= _PID_CEILING:
            raise ValueError('an activity entry\'s pid is a process id above zero')
        command_line = raw['command_line']
        if not isinstance(command_line, list) or not all(isinstance(a, str) for a in command_line):
            raise ValueError('an activity entry\'s command_line is a list of text')
        return cls(
            activity_id=_text(raw, 'activity_id'),
            activity_kind=ActivityKind(raw['activity_kind']),
            activity_state=ActivityState(raw['activity_state']),
            activity_label=_text(raw, 'activity_label'),
            pid=pid,
            process_started_at=_finite(raw, 'process_started_at'),
            command_line=command_line,
            started_at=started_at,
            started_monotonic=_finite(raw, 'started_monotonic'),
            working_tree=_text(raw, 'working_tree'),
            console_session=_text(raw, 'console_session', default=''),
            git_commit=_optional_text(raw, 'git_commit'),
            origin=RunOrigin(**origin) if origin is not None else None,
            run_id=_optional_text(raw, 'run_id'),
            sweep_id=_optional_text(raw, 'sweep_id'),
            parent_activity_id=_optional_text(raw, 'parent_activity_id'),
        )


def _text(raw: Dict[str, Any], key: str, default: Optional[str] = None) -> str:
    """
    A text field of an entry.

    Args:
        raw: The parsed entry
        key: The field
        default: What a missing field reads as; None makes the field required

    Returns:
        The text; a value of another type raises ValueError
    """
    value = raw[key] if default is None else raw.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f'an activity entry\'s {key} is text')
    return value


def _optional_text(raw: Dict[str, Any], key: str) -> Optional[str]:
    """
    A text field of an entry that may be absent or null.

    Args:
        raw: The parsed entry
        key: The field

    Returns:
        The text or None; a value of another type raises ValueError
    """
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f'an activity entry\'s {key} is text or null')
    return value


def _finite(raw: Dict[str, Any], key: str) -> float:
    """
    A number field of an entry.

    Args:
        raw: The parsed entry
        key: The field

    Returns:
        The number; one that is no finite number raises ValueError
    """
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'an activity entry\'s {key} is a finite number')
    return float(value)


@dataclass
class ActivityMemory:
    """
    What an activity's processes hold.

    `rss_bytes` is resident memory summed over the processes counted toward the activity — an
    upper bound, because a forked child shares pages with its parent and both count them. None
    when a process could not be read; never 0 in its place.
    """
    rss_bytes: Optional[int]
    process_count: int


@dataclass
class ActivityListing:
    """
    One reading of the registry.

    `unreadable_activity_ids` are entries this code cannot read or judge: their activity holds its
    lock but their file is foreign — written by another code tree, for example — or their lock
    cannot be asked about. Either way they are left as they are.
    """
    entries: List[ActivityEntry] = field(default_factory=list)
    unreadable_activity_ids: List[str] = field(default_factory=list)


@dataclass
class ActivitySnapshot:
    """
    The registry, what each activity holds and the machine's memory, read at one instant.

    `measured_at` is taken first; everything else is read right after it.
    """
    measured_at: datetime
    listing: ActivityListing
    memory: Dict[str, ActivityMemory]
    machine_memory: MemoryReading


@dataclass
class FreeMemoryCheck:
    """
    The answer to "is enough memory free?" (#603), and who holds the memory when it is not.

    Args:
        verdict: The answer, taken from the machine's memory alone
        snapshot: Who holds the memory — read only when the answer is no
        list_error: Why that reading failed, where it did; it never changes the answer
    """
    verdict: FreeMemoryVerdict
    snapshot: Optional[ActivitySnapshot] = None
    list_error: Optional[str] = None
