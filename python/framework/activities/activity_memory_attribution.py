"""
FiniexTestingIDE - Activity Memory Attribution
Which activity holds which process, and what each activity's processes hold together (#603).

Every process counts once, toward the nearest activity above it in the process tree: a backtest's
scenario workers toward the backtest, the test suite's pytest processes toward the suite. Where
several activities run in ONE process — a sweep and the backtest of its current combination —
the process belongs to the outermost of them, the one that started it; the inner activity holds
no process of its own, and its memory is its parent's. So the rows of a list add up without
overlap, and nested rows say where their memory went rather than counting it twice.
"""

from typing import Dict, List, Optional

from python.framework.types.activity_registry_types import ActivityEntry, ActivityMemory
from python.framework.types.process_table_types import ProcessReading


def attribute_memory(
    entries: List[ActivityEntry],
    table: Dict[int, ProcessReading],
) -> Dict[str, ActivityMemory]:
    """
    Count every process of the machine toward the activity it belongs to.

    Args:
        entries: The running activities
        table: One reading of the process table

    Returns:
        Each activity's memory, keyed by activity id; an activity whose own process is gone and
        that holds no other one is `rss_bytes` None — not readable, never 0
    """
    owners = _owners_by_pid(entries)
    rss: Dict[str, int] = {entry.activity_id: 0 for entry in entries}
    counts: Dict[str, int] = {entry.activity_id: 0 for entry in entries}
    unreadable: Dict[str, bool] = {entry.activity_id: False for entry in entries}
    resolved: Dict[int, Optional[str]] = {}
    for pid, reading in table.items():
        owner = _nearest_owner(pid, table, owners, resolved)
        if owner is None:
            continue
        counts[owner] += 1
        if reading.rss_bytes is None:
            unreadable[owner] = True
        else:
            rss[owner] += reading.rss_bytes

    memory: Dict[str, ActivityMemory] = {}
    for entry in entries:
        activity_id = entry.activity_id
        nested_in_parent = owners.get(entry.pid) != activity_id and entry.pid in table
        gone = counts[activity_id] == 0 and not nested_in_parent
        memory[activity_id] = ActivityMemory(
            rss_bytes=None if gone or unreadable[activity_id] else rss[activity_id],
            process_count=counts[activity_id],
        )
    return memory


def _owners_by_pid(entries: List[ActivityEntry]) -> Dict[int, str]:
    """
    The activity that owns each process an activity runs in: the outermost one there.

    Args:
        entries: The running activities

    Returns:
        Activity id by process id
    """
    by_id = {entry.activity_id: entry for entry in entries}
    owners: Dict[int, str] = {}
    for entry in sorted(entries, key=lambda e: e.started_at):
        parent = by_id.get(entry.parent_activity_id) if entry.parent_activity_id else None
        if parent is not None and parent.pid == entry.pid:
            continue
        owners.setdefault(entry.pid, entry.activity_id)
    return owners


def _nearest_owner(
    pid: int,
    table: Dict[int, ProcessReading],
    owners: Dict[int, str],
    resolved: Dict[int, Optional[str]],
) -> Optional[str]:
    """
    The activity a process counts toward: the first owner on its way up the process tree.

    Args:
        pid: The process
        table: One reading of the process table
        owners: Activity id by the process id it runs in
        resolved: Answers already found in this reading, filled in on the way

    Returns:
        The owning activity's id, or None when no activity lies above the process
    """
    path: List[int] = []
    current: Optional[int] = pid
    answer: Optional[str] = None
    # Bounded by the table's size: a process table read in one pass can still hold a cycle of
    # reused ids, and the walk must end either way.
    for _ in range(len(table) + 1):
        if current is None or current <= 0:
            break
        if current in resolved:
            answer = resolved[current]
            break
        if current in owners:
            answer = owners[current]
            break
        path.append(current)
        reading = table.get(current)
        current = reading.ppid if reading is not None else None
    for visited in path:
        resolved[visited] = answer
    return answer
