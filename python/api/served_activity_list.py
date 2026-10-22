"""
What runs on this machine, as the API serves it (#603).

No path of the machine leaves: a command line's paths — the value of a `--name=value` option
included — are named relative to the code tree the activity runs from, a tree is `.` for the one
this server runs from and its directory's name otherwise, and a console session named by a path
leaves by its last part. A read removes nothing but the entries of activities that have ended.
"""

import os
import shlex
import time
from typing import Optional

from python.api.served_machine_resources import describe_machine_memory
from python.framework.types.activity_registry_types import (
    ActivityEntry,
    ActivityKind,
    ActivitySnapshot,
)
from python.framework.types.api.api_types import ActivityListResponse, ActivityRow
from python.framework.types.config_types.machine_resources_config_types import (
    MachineResourcesConfig,
)
from python.framework.utils.served_path_utils import (
    normal_path,
    relative_served_path,
    served_command_argument,
)


def served_activity_list(
    snapshot: ActivitySnapshot,
    thresholds: MachineResourcesConfig,
    own_tree: Optional[str],
) -> ActivityListResponse:
    """
    The snapshot as the API serves it.

    Args:
        snapshot: One reading of the registry and the machine
        thresholds: Where the memory's `warn` and `critical` begin
        own_tree: The code tree this server runs from

    Returns:
        The list, newest activity first
    """
    machine_memory, level, reasons = describe_machine_memory(snapshot.machine_memory, thresholds)
    now_monotonic = time.monotonic()
    rows = [_served_row(entry, snapshot, own_tree, now_monotonic)
            for entry in snapshot.listing.entries]
    return ActivityListResponse(
        measured_at=snapshot.measured_at.isoformat(),
        machine_memory=machine_memory,
        pressure_level=level,
        pressure_reasons=reasons,
        server_activity_id=_server_activity_id(snapshot),
        unreadable_activity_ids=list(snapshot.listing.unreadable_activity_ids),
        activities=rows,
    )


def _served_row(entry: ActivityEntry, snapshot: ActivitySnapshot, own_tree: Optional[str],
                now_monotonic: float) -> ActivityRow:
    """
    One activity as served.

    Args:
        entry: The activity's entry
        snapshot: The reading it belongs to
        own_tree: The code tree this server runs from
        now_monotonic: The machine's monotonic clock at serving

    Returns:
        The row
    """
    memory = snapshot.memory.get(entry.activity_id)
    origin = entry.origin
    roots = [normal_path(entry.working_tree)[0]]
    return ActivityRow(
        activity_id=entry.activity_id,
        activity_kind=entry.activity_kind,
        activity_state=entry.activity_state,
        activity_label=entry.activity_label,
        run_id=entry.run_id,
        sweep_id=entry.sweep_id,
        parent_activity_id=entry.parent_activity_id,
        pid=entry.pid,
        command_line=shlex.join(served_command_argument(argument, roots)
                                for argument in entry.command_line),
        started_at=entry.started_at.isoformat(),
        uptime_s=round(max(now_monotonic - entry.started_monotonic, 0.0), 1),
        working_tree=_served_tree(entry.working_tree, own_tree),
        git_commit=entry.git_commit,
        origin_channel=origin.channel if origin is not None else None,
        origin_client=origin.client if origin is not None else None,
        origin_principal=origin.principal if origin is not None else None,
        origin_host=origin.host if origin is not None else None,
        # A session variable such as TMUX or SSH_TTY holds a path; it leaves by its last part
        console_session=relative_served_path(entry.console_session, []) or None,
        rss_bytes=memory.rss_bytes if memory is not None else None,
        process_count=memory.process_count if memory is not None else 0,
    )


def _served_tree(working_tree: str, own_tree: Optional[str]) -> str:
    """
    A code tree as it may leave the machine.

    Args:
        working_tree: The tree the activity runs from, as recorded
        own_tree: The tree this server runs from

    Returns:
        `.` for the server's own tree, the directory's name for any other
    """
    tree = normal_path(working_tree)[0]
    if own_tree is not None and tree == normal_path(own_tree)[0]:
        return '.'
    return tree.name


def _server_activity_id(snapshot: ActivitySnapshot) -> Optional[str]:
    """
    The row of the server answering — found from its own process, never from an inherited value.

    Under `--reload` the server answers from a child of the process that registered, so the
    parent counts too.

    Args:
        snapshot: The reading

    Returns:
        The server's activity id, or None when it is not in the list
    """
    own = {os.getpid(), os.getppid()}
    for entry in snapshot.listing.entries:
        if entry.activity_kind is ActivityKind.API_SERVER and entry.pid in own:
            return entry.activity_id
    return None
