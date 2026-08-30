"""
FiniexTestingIDE - Run Index CLI
Command-line tools for the derived run index

Usage:
    python python/cli/run_index_cli.py rebuild
    python python/cli/run_index_cli.py status
"""

import argparse
import sys

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.store.run_index import RunIndex


class RunIndexCli:
    """Command-line interface for the run index."""

    def __init__(self):
        """Initialize CLI with paths from AppConfigManager."""
        self._file_logging = AppConfigManager().get_file_logging_config_object()
        self._index = RunIndex(self._file_logging.run_index)

    def cmd_rebuild(self) -> int:
        """
        Rebuild the index from the run headers on disk.

        Returns:
            Process exit code
        """
        print('\n' + '=' * 80)
        print('🔄 Rebuilding Run Index')
        print('=' * 80 + '\n')
        count = self._index.rebuild(self._file_logging.run_logs)
        print(f'✅ {count} run(s) indexed → {self._file_logging.run_index}')
        duplicates = self._index.duplicate_ids()
        if duplicates:
            print(f'\n⚠️  {len(duplicates)} id(s) appear more than once — these predate the '
                  f'distinct id and collided when they were minted. Every report route resolves '
                  f'the FIRST of each pair:')
            for run_id in duplicates:
                print(f'      {run_id}')
            print('    Re-run or remove one of each pair to clear it.')
        print()
        return 0

    def cmd_status(self) -> int:
        """
        Show what the index currently holds.

        Returns:
            Process exit code
        """
        runs = self._index.list_runs()
        print('\n' + '=' * 80)
        print(f'📇 Run Index — {len(runs)} run(s) · {self._file_logging.run_index}')
        print('=' * 80 + '\n')
        for run in runs[:20]:
            reports = '✅' if run.has_reports else '➖'
            print(f'  {reports}  {run.run_id}  {run.group:<12}  {run.name}')
        if len(runs) > 20:
            print(f'  … and {len(runs) - 20} more')
        print()
        return 0


def main() -> int:
    """
    Parse arguments and dispatch.

    Returns:
        Process exit code
    """
    parser = argparse.ArgumentParser(
        description='Run Index CLI (the derived index the API reads)')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    subparsers.add_parser(
        'rebuild', help='Rebuild the index from the run headers on disk')
    subparsers.add_parser('status', help='Show what the index holds')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    cli = RunIndexCli()
    return {'rebuild': cli.cmd_rebuild, 'status': cli.cmd_status}[args.command]()


if __name__ == '__main__':
    sys.exit(main())
