"""
FiniexTestingIDE - Activity List views (#603)

What runs on this machine, and the answer to a memory check. Formatting only: the list comes from
`take_activity_snapshot`, the answer from `take_memory_check`.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from python.framework.types.activity_registry_types import (
    ActivityEntry,
    ActivityMemory,
    ActivitySnapshot,
    FreeMemoryCheck,
)
from python.system.ui.ui_render_utils import BYTES_PER_GB

# The width of the WHAT column; a longer label keeps its start and its end, where a sweep's
# combination names itself (`…_c003`)
_LABEL_WIDTH = 34


def render_activity_list(snapshot: ActivitySnapshot, own_tree: Optional[str]) -> None:
    """
    Every running activity, a nested one under its parent, and the machine's memory.

    Args:
        snapshot: One reading of the registry and the machine
        own_tree: The code tree this command runs from; its activities show `.` as their tree
    """
    entries = snapshot.listing.entries
    print()
    if not entries:
        print('  Nothing runs on this machine.')
    else:
        print(f'  {"KIND":<22} {"WHAT":<{_LABEL_WIDTH}} {"SINCE (UTC)":<11} {"TREE":<16} '
              f'{"STARTED BY":<11} {"MEMORY":>10}')
        by_id = {entry.activity_id: entry for entry in entries}
        for entry in entries:
            if entry.parent_activity_id in by_id:
                continue
            _render_row(entry, 0, snapshot, by_id, own_tree)
            _render_children(entry, 1, snapshot, by_id, own_tree)
    for activity_id in snapshot.listing.unreadable_activity_ids:
        print(f'  ⚠️ {activity_id}: its entry cannot be read or judged by this code tree')
    machine = snapshot.machine_memory
    print(f'\n  machine: {_gb(machine.limit_bytes)} · {_gb(machine.free_bytes())} free '
          f'({machine.limit_source.value} limit)\n')


def render_free_memory_check(check: FreeMemoryCheck, own_tree: Optional[str]) -> None:
    """
    Whether the memory asked for is free; when it is not, who holds the memory.

    Args:
        check: The answer, and who holds the memory when it is no
        own_tree: The code tree this command runs from
    """
    verdict = check.verdict
    if verdict.enough:
        print(f'✅ {_gb(verdict.free_bytes)} free — at least {_gb(verdict.required_bytes)} asked')
        return
    print(f'❌ {_gb(verdict.free_bytes)} free — {_gb(verdict.required_bytes)} asked. '
          'What holds memory:')
    if check.snapshot is not None:
        render_activity_list(check.snapshot, own_tree)
    else:
        print(f'\n  The activity list could not be read: {check.list_error}\n')


def _render_children(parent: ActivityEntry, depth: int, snapshot: ActivitySnapshot,
                     by_id: Dict[str, ActivityEntry], own_tree: Optional[str]) -> None:
    """
    The activities started inside one, each under its parent.

    Args:
        parent: The activity whose children are rendered
        depth: How far they are indented
        snapshot: The reading
        by_id: The activities by id
        own_tree: The code tree this command runs from
    """
    for child in snapshot.listing.entries:
        if child.parent_activity_id == parent.activity_id:
            _render_row(child, depth, snapshot, by_id, own_tree)
            _render_children(child, depth + 1, snapshot, by_id, own_tree)


def _render_row(entry: ActivityEntry, depth: int, snapshot: ActivitySnapshot,
                by_id: Dict[str, ActivityEntry], own_tree: Optional[str]) -> None:
    """
    One activity's line.

    Args:
        entry: The activity
        depth: How far it is indented under its parent
        snapshot: The reading — what each activity holds, and when it was taken
        by_id: The activities by id
        own_tree: The code tree this command runs from
    """
    kind = ('  ' * (depth - 1) + '└ ' if depth else '') + entry.activity_kind.value.replace('_', ' ')
    since = _since_text(entry.started_at, snapshot.measured_at)
    tree = _tree_text(entry, by_id, own_tree)
    started_by = entry.origin.channel.value if entry.origin is not None else '—'
    print(f'  {kind:<22} {_label_text(entry.activity_label):<{_LABEL_WIDTH}} {since:<11} '
          f'{tree:<16} {started_by:<11} {_memory_text(entry, snapshot.memory, by_id):>10}')


def _since_text(started_at: datetime, measured_at: datetime) -> str:
    """
    When an activity started, in UTC: the time on the day of the reading, the date before it.

    Args:
        started_at: The activity's start
        measured_at: When the reading was taken

    Returns:
        `14:02`, or `10-17 14:02` for a start on an earlier day
    """
    if started_at.date() == measured_at.date():
        return started_at.strftime('%H:%M')
    return started_at.strftime('%m-%d %H:%M')


def _label_text(label: str) -> str:
    """
    A label as it fits its column: whole, or its start and its end around an ellipsis.

    Args:
        label: The activity's label

    Returns:
        The label as shown
    """
    if len(label) <= _LABEL_WIDTH:
        return label
    tail = _LABEL_WIDTH // 2
    return f'{label[:_LABEL_WIDTH - tail - 1]}…{label[-tail:]}'


def _tree_text(entry: ActivityEntry, by_id: Dict[str, ActivityEntry],
               own_tree: Optional[str]) -> str:
    """
    The code tree an activity runs from: `.` for this one, its directory's name otherwise, and
    nothing for an activity that runs in its parent's tree.

    Args:
        entry: The activity
        by_id: The activities by id
        own_tree: The code tree this command runs from

    Returns:
        The tree as shown
    """
    parent = by_id.get(entry.parent_activity_id) if entry.parent_activity_id else None
    if parent is not None and parent.working_tree == entry.working_tree:
        return ''
    if own_tree is not None and entry.working_tree == own_tree:
        return '.'
    return Path(entry.working_tree).name


def _memory_text(entry: ActivityEntry, memory: Dict[str, ActivityMemory],
                 by_id: Dict[str, ActivityEntry]) -> str:
    """
    What an activity's processes hold, as shown.

    Args:
        entry: The activity
        memory: What each activity holds
        by_id: The activities by id

    Returns:
        The size, `(in <parent>)` for an activity running inside its parent's process, or `—`
        where it could not be read
    """
    held = memory.get(entry.activity_id)
    parent = by_id.get(entry.parent_activity_id) if entry.parent_activity_id else None
    if held is not None and held.process_count == 0 and parent is not None \
            and parent.pid == entry.pid:
        return f'(in {parent.activity_kind.value.replace("_", " ")})'
    if held is None or held.rss_bytes is None:
        return '—'
    return _gb(held.rss_bytes)


def _gb(size_bytes: int) -> str:
    """
    A size in GB of 1024³ bytes, with one decimal — the unit every memory figure here uses.

    Args:
        size_bytes: The size

    Returns:
        The size as shown
    """
    return f'{size_bytes / BYTES_PER_GB:.1f} GB'
