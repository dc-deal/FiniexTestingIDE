"""
FiniexTestingIDE - Run Config CLI (#538)

Read the run-config store: what is registered, what one configuration has been, and where its
frozen bytes are.

    python python/cli/run_config_cli.py list
    python python/cli/run_config_cli.py history <source file name>
    python python/cli/run_config_cli.py show <config id>

Parameter reception only — every figure is derived in the store and rendered in
`reporting/console/run_config_summary.py` (§13).
"""

import argparse
import sys
from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.console.run_config_summary import (
    render_run_config_history,
    render_run_config_list,
)
from python.framework.store.run_config_store import RunConfigStore


class RunConfigCli:
    """Read-only access to the registered run configurations."""

    def __init__(self):
        self._store = RunConfigStore(Path(AppConfigManager().get_run_configs_path()))

    def cmd_list(self) -> int:
        """
        Print every registered version.

        Returns:
            Process exit code
        """
        render_run_config_list(self._store.get_index().entries())
        return 0

    def cmd_history(self, source_name: str) -> int:
        """
        Print the versions one source file has had.

        Args:
            source_name: The config file name

        Returns:
            Process exit code
        """
        render_run_config_history(self._store.history(source_name))
        return 0

    def cmd_show(self, config_id: str) -> int:
        """
        Print where one registered version's frozen bytes live.

        Args:
            config_id: The content id, in full or as its leading characters

        Returns:
            Process exit code — 1 when the id matches nothing
        """
        entries = [e for e in self._store.get_index().entries()
                   if e.config_id.startswith(config_id)]
        if not entries:
            print(f'No registered configuration starts with {config_id!r}')
            return 1
        if len(entries) > 1:
            print(f'{config_id!r} is ambiguous — {len(entries)} versions start with it')
            return 1

        entry = entries[0]
        frozen = self._store.frozen_path_of(entry.config_id)
        print(f'\n  config_id   {entry.config_id}')
        print(f'  kind        {entry.kind}')
        print(f'  source      {entry.source_name}  ({entry.source_path})')
        print(f'  frozen      {frozen}')
        print(f'  param_hash  {entry.param_hash}')
        print(f'  scope_hash  {entry.scope_hash or "—  (a profile has no scenario list)"}')
        print(f'  first seen  {entry.first_seen}')
        print(f'  runs        {entry.run_count}\n')
        return 0


def main() -> int:
    """
    Parse the command line and dispatch.

    Returns:
        Process exit code
    """
    parser = argparse.ArgumentParser(description='Read the run-config store (#538)')
    subparsers = parser.add_subparsers(dest='command', required=True)

    subparsers.add_parser('list', help='Every registered configuration version')

    history_parser = subparsers.add_parser(
        'history', help='The versions one source file has had')
    history_parser.add_argument('source_name', help='Config file name, e.g. my_set.json')

    show_parser = subparsers.add_parser('show', help='One version in full')
    show_parser.add_argument('config_id', help='Content id, or its leading characters')

    args = parser.parse_args()
    cli = RunConfigCli()

    if args.command == 'list':
        return cli.cmd_list()
    if args.command == 'history':
        return cli.cmd_history(args.source_name)
    return cli.cmd_show(args.config_id)


if __name__ == '__main__':
    sys.exit(main())
