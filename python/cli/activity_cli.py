"""
FiniexTestingIDE - Activity CLI
What runs on this machine (#603), and whether enough memory is free to start another run.

Parameter reception only: the list comes from `take_activity_snapshot`, the verdict from
`validators/free_memory_validator.py`, and both are rendered in
`reporting/console/activity_list_summary.py`.

Usage:
    python python/cli/activity_cli.py list
    python python/cli/activity_cli.py check --min-free-gb 8

`check` exits 0 when at least that much memory is free and 1 when not — the question a wrapper
script asks before it starts a run.
"""

import argparse
import sys

from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.activities.activity_snapshot import take_activity_snapshot
from python.framework.reporting.console.activity_list_summary import (
    render_activity_list,
    render_free_memory_check,
)
from python.framework.utils.git_info_utils import get_framework_root
from python.framework.utils.machine_resources_reader import MachineResourcesReader
from python.framework.validators.free_memory_validator import judge_free_memory


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
                             get_framework_root())
        return 0

    def cmd_check(self, min_free_gb: float) -> int:
        """
        Answer whether at least `min_free_gb` of memory is free.

        Args:
            min_free_gb: The memory the next run needs, in GiB

        Returns:
            0 when enough is free, 1 when not
        """
        snapshot = take_activity_snapshot(self._reader, self._machine_reader)
        verdict = judge_free_memory(snapshot.machine_memory, min_free_gb)
        render_free_memory_check(verdict, snapshot, get_framework_root())
        return 0 if verdict.enough else 1


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
        'check', help='Exit 0 when enough memory is free, 1 when not')
    check_parser.add_argument(
        '--min-free-gb', type=float, required=True, dest='min_free_gb',
        help='The memory the next run needs, in GiB')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    cli = ActivityCli()
    if args.command == 'list':
        return cli.cmd_list()
    return cli.cmd_check(args.min_free_gb)


if __name__ == '__main__':
    sys.exit(main())
