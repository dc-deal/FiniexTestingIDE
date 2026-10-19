"""
FiniexTestingIDE - Activity Registry Types
What runs on this machine (#603): one entry per running activity, written by the activity and read
by the list.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, List, Optional, Self

from python.framework.types.machine_resources_types import MemoryReading
from python.framework.types.run_origin_types import RunOrigin

# The entry's own format. A reader meets an entry written by another code tree — another
# checkout or release on the same machine — and lists one it cannot read as unreadable rather
# than guessing at it.
ACTIVITY_ENTRY_SCHEMA = 1


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
            The entry; a missing or foreign field raises, which the reader reports as unreadable
        """
        if raw.get('schema_version') != ACTIVITY_ENTRY_SCHEMA:
            raise ValueError(f"activity entry schema {raw.get('schema_version')!r} is not "
                             f'{ACTIVITY_ENTRY_SCHEMA}')
        origin = raw.get('origin')
        return cls(
            activity_id=raw['activity_id'],
            activity_kind=ActivityKind(raw['activity_kind']),
            activity_state=ActivityState(raw['activity_state']),
            activity_label=raw['activity_label'],
            pid=int(raw['pid']),
            process_started_at=float(raw['process_started_at']),
            command_line=list(raw['command_line']),
            started_at=datetime.fromisoformat(raw['started_at']),
            started_monotonic=float(raw['started_monotonic']),
            working_tree=raw['working_tree'],
            console_session=raw.get('console_session', ''),
            git_commit=raw.get('git_commit'),
            origin=RunOrigin(**origin) if origin is not None else None,
            run_id=raw.get('run_id'),
            sweep_id=raw.get('sweep_id'),
            parent_activity_id=raw.get('parent_activity_id'),
        )


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

    `unreadable_activity_ids` are entries whose activity still holds its lock but whose file this
    code cannot read — written by another code tree, for example.
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
