"""
FiniexTestingIDE - Activity List views (#603)

What runs on this machine, and the answer to a memory check. Formatting only: the list comes from
`take_activity_snapshot`, the verdict from `validators/free_memory_validator.py`.
"""

from pathlib import Path
from typing import Dict, List, Optional

from python.framework.types.activity_registry_types import (
    ActivityEntry,
    ActivityMemory,
    ActivitySnapshot,
)
from python.framework.types.machine_resources_types import FreeMemoryVerdict

_GIB = 1024 ** 3


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
        print(f'  {"KIND":<22} {"WHAT":<34} {"SINCE":<10} {"TREE":<16} {"STARTED BY":<11} '
              f'{"MEMORY":>10}')
        by_id = {entry.activity_id: entry for entry in entries}
        for entry in entries:
            if entry.parent_activity_id in by_id:
                continue
            _render_row(entry, 0, snapshot.memory, by_id, own_tree)
            _render_children(entry, 1, entries, snapshot.memory, by_id, own_tree)
    for activity_id in snapshot.listing.unreadable_activity_ids:
        print(f'  ⚠️ {activity_id}: running, but its entry cannot be read by this code tree')
    machine = snapshot.machine_memory
    free = max(machine.limit_bytes - machine.used_bytes, 0)
    print(f'\n  machine: {_gb(machine.limit_bytes)} · {_gb(free)} free '
          f'({machine.limit_source.value} limit)\n')


def render_free_memory_check(verdict: FreeMemoryVerdict, snapshot: ActivitySnapshot,
                             own_tree: Optional[str]) -> None:
    """
    Whether the memory asked for is free; when it is not, who holds the memory.

    Args:
        verdict: The check's verdict
        snapshot: The reading the verdict was taken on
        own_tree: The code tree this command runs from
    """
    if verdict.enough:
        print(f'✅ {_gb(verdict.free_bytes)} free — at least {_gb(verdict.required_bytes)} asked')
        return
    print(f'❌ {_gb(verdict.free_bytes)} free — {_gb(verdict.required_bytes)} asked. '
          'What holds memory:')
    render_activity_list(snapshot, own_tree)


def _render_children(parent: ActivityEntry, depth: int, entries: List[ActivityEntry],
                     memory: Dict[str, ActivityMemory], by_id: Dict[str, ActivityEntry],
                     own_tree: Optional[str]) -> None:
    """
    The activities started inside one, each under its parent.

    Args:
        parent: The activity whose children are rendered
        depth: How far they are indented
        entries: Every running activity
        memory: What each holds
        by_id: The activities by id
        own_tree: The code tree this command runs from
    """
    for child in entries:
        if child.parent_activity_id == parent.activity_id:
            _render_row(child, depth, memory, by_id, own_tree)
            _render_children(child, depth + 1, entries, memory, by_id, own_tree)


def _render_row(entry: ActivityEntry, depth: int, memory: Dict[str, ActivityMemory],
                by_id: Dict[str, ActivityEntry], own_tree: Optional[str]) -> None:
    """
    One activity's line.

    Args:
        entry: The activity
        depth: How far it is indented under its parent
        memory: What each activity holds
        by_id: The activities by id
        own_tree: The code tree this command runs from
    """
    kind = ('  ' * (depth - 1) + '└ ' if depth else '') + entry.activity_kind.value.replace('_', ' ')
    since = entry.started_at.strftime('%H:%M UTC')
    tree = _tree_text(entry, by_id, own_tree)
    started_by = entry.origin.channel.value if entry.origin is not None else '—'
    print(f'  {kind:<22} {entry.activity_label[:34]:<34} {since:<10} {tree:<16} '
          f'{started_by:<11} {_memory_text(entry, memory, by_id):>10}')


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
    A size in GiB with one decimal.

    Args:
        size_bytes: The size

    Returns:
        The size as shown
    """
    return f'{size_bytes / _GIB:.1f} GB'
