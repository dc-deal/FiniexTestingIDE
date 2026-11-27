"""
FiniexTestingIDE - Documentation Search CLI
Searches the whole documentation tree, and any further collection set up for this installation

Usage:
    python python/cli/docs_search_cli.py search pending order counters [--hits 8]
    python python/cli/docs_search_cli.py search pending order counters --root /path/to/my_collection
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.docs_search.docs_search_console import DocsSearchConsole

# The documentation tree. Derived from this file's location, the way the served set is derived
# from its module's: the documents ship with the code that searches them.
DOCS_ROOT = Path(__file__).resolve().parents[2] / 'docs'


class DocsSearchCli:
    """Command-line interface for the documentation search."""

    def cmd_search(self, query: str, hits: int, root: Optional[Path] = None) -> int:
        """
        Search the documentation tree and the configured extra searches, or one folder alone.

        Args:
            query: The search term
            hits: How many passages to show at most
            root: A folder searched on its own, one level deep, with no extra search after it

        Returns:
            Process exit code
        """
        if root is not None:
            stamps = AppConfigManager().get_docs_search_snapshot_stamps()
            return DocsSearchConsole(root, [], stamps).run_folder(query, hits)
        extra_commands = AppConfigManager().get_docs_search_extra_commands()
        return DocsSearchConsole(DOCS_ROOT, extra_commands).run(query, hits)


def main() -> None:
    """Parse the command line and run the chosen command."""
    parser = argparse.ArgumentParser(description='Search the documentation')
    subparsers = parser.add_subparsers(dest='command', required=True)

    search = subparsers.add_parser(
        'search', help='Rank the documentation passages that answer a search term')
    search.add_argument('query', nargs='+', help='What to search for')
    search.add_argument('--hits', type=int, default=8,
                        help='How many documentation passages to show (default 8)')
    search.add_argument('--root', type=Path, default=None,
                        help='Search this folder alone, one level deep, instead of the '
                             'documentation tree; the form an extra search takes')

    args = parser.parse_args()
    cli = DocsSearchCli()
    if args.command == 'search':
        sys.exit(cli.cmd_search(' '.join(args.query), args.hits, args.root))


if __name__ == '__main__':
    main()
