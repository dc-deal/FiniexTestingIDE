"""
FiniexTestingIDE - Config Directory CLI (#554)

Every configuration that can start a run — scenario sets and AutoTrader profiles — and what one
of them declares. Parameter reception only; the listing is built by `ConfigDirectory` and rendered
in `reporting/console/config_directory_summary.py` (§13), the same model the API serves.

Deliberately lean: it imports the directory and nothing of the batch pipeline, whose imports alone
take ~5.7 s. Running the real loader over the sets is `strategy_runner_cli.py validate`.

Usage:
    python python/cli/config_directory_cli.py list [--kind scenario_set|autotrader_profile] [--refresh]
    python python/cli/config_directory_cli.py show <file>
"""

import argparse
import sys

from python.framework.config_directory.config_directory import ConfigDirectory
from python.framework.reporting.console.config_directory_summary import (
    render_config_directory,
    render_config_directory_entry,
)
from python.framework.types.config_directory_types import ConfigKind


class ConfigDirectoryCli:
    """Parameter reception for the config directory."""

    def __init__(self):
        self._directory = ConfigDirectory()

    def cmd_list(self, kind: str = None, refresh: bool = False) -> None:
        """
        Every configuration file, grouped by kind.

        Args:
            kind: Only this kind, or None for both
            refresh: Walk the roots now instead of serving a recent reading
        """
        render_config_directory(self._directory.list_configs(refresh=refresh),
                                ConfigKind(kind) if kind else None)

    def cmd_show(self, file: str) -> int:
        """
        One configuration file.

        Args:
            file: The file name, as the list names it

        Returns:
            Exit code — 1 when the directory lists no such file
        """
        detail = self._directory.detail(file)
        if detail is None:
            print(f"❌ No configuration file '{file}' in the directory")
            return 1
        render_config_directory_entry(detail)
        return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Every configuration that can start a run (#554)')
    subparsers = parser.add_subparsers(dest='command', required=True)

    list_parser = subparsers.add_parser('list', help='Every scenario set and AutoTrader profile')
    list_parser.add_argument('--kind', choices=[kind.value for kind in ConfigKind], default=None,
                             help='Only one kind')
    list_parser.add_argument('--refresh', action='store_true',
                             help='Walk the configuration roots now')

    show_parser = subparsers.add_parser('show', help='One configuration file in detail')
    show_parser.add_argument('file', help='The file name, as the list names it')

    args = parser.parse_args()
    cli = ConfigDirectoryCli()
    if args.command == 'list':
        cli.cmd_list(kind=args.kind, refresh=args.refresh)
    elif args.command == 'show':
        sys.exit(cli.cmd_show(args.file))


if __name__ == '__main__':
    main()
