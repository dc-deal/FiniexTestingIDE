"""
FiniexTestingIDE - Activity CLI
What runs on this machine (#603), and whether enough memory is free to start another run.

Parameter reception only: the list comes from `take_activity_snapshot`, the answer from
`take_memory_check`, and both are rendered in `reporting/console/activity_list_summary.py`.

Usage:
    python python/cli/activity_cli.py list
    python python/cli/activity_cli.py check --min-free-gb 8

`check` exits 0 when at least that much memory is free, 1 when not, and 2 when it gives no answer
— a usage error, or a fault before the memory could be judged. The question a wrapper script asks
before it starts a run; a list that cannot be read never changes the answer.
"""

import argparse
import math
import sys
import traceback

from python.framework.activities.activity_registry import code_tree_of_this_process
from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.activities.activity_snapshot import take_activity_snapshot, take_memory_check
from python.framework.reporting.console.activity_list_summary import (
    render_activity_list,
    render_free_memory_check,
)
from python.framework.utils.machine_resources_reader import MachineResourcesReader
from python.system.ui.ui_render_utils import BYTES_PER_GB


class ActivityCli:
    """Command-line interface for the list of what runs on this machine."""

    def __init__(self):
        self._reader = ActivityRegistryReader()
        self._machine_reader = MachineResourcesReader()

    def cmd_list(self) -> int:
        """
        Print every running activity with what its processes hold, and the machine's memory.

        Returns:
            Process exit code
        """
        render_activity_list(take_activity_snapshot(self._reader, self._machine_reader),
                             code_tree_of_this_process())
        return 0

    def cmd_check(self, min_free_gb: float) -> int:
        """
        Answer whether at least `min_free_gb` of memory is free.

        Args:
            min_free_gb: The memory the next run needs, in GB of 1024³ bytes like every memory
                figure here

        Returns:
            0 when enough is free, 1 when not
        """
        check = take_memory_check(self._reader, self._machine_reader,
                                  int(min_free_gb * BYTES_PER_GB))
        render_free_memory_check(check, code_tree_of_this_process())
        return 0 if check.verdict.enough else 1


def _size_gb(text: str) -> float:
    """
    A size in GB as typed on the command line.

    Args:
        text: The argument

    Returns:
        The size, when it is a finite number above zero
    """
    size = float(text)
    if not math.isfinite(size) or size <= 0 or not math.isfinite(size * BYTES_PER_GB):
        raise argparse.ArgumentTypeError(f'{text!r} is not a size above zero')
    return size


def main() -> int:
    """
    Parse arguments and dispatch.

    Returns:
        Process exit code
    """
    parser = argparse.ArgumentParser(
        description='Activity CLI — what runs on this machine (#603)')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    subparsers.add_parser(
        'list', help='Show every running activity, who started it and what it holds')
    check_parser = subparsers.add_parser(
        'check', help='Exit 0 when enough memory is free, 1 when not, 2 when there is no answer')
    check_parser.add_argument(
        '--min-free-gb', type=_size_gb, required=True, dest='min_free_gb',
        help='The memory the next run needs, in GB (1024³ bytes)')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    if args.command == 'list':
        return ActivityCli().cmd_list()
    try:
        return ActivityCli().cmd_check(args.min_free_gb)
    except Exception:
        # No answer: a fault must never read as "not enough memory" to the script that asks
        traceback.print_exc()
        return 2


if __name__ == '__main__':
    sys.exit(main())
